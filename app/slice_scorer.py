import trimesh, numpy as np #loads and sliced 3d mesh files
from pathlib import Path 
from tqdm import tqdm
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error
from scipy.stats import spearmanr
import joblib #saves trained model to disk


def slice_model(obj_path, n_slices=100):
    #purpose: loads a 3d model and cuts it into cross sectional slices along each axis and collect geometric measurements from each slice
    loaded = trimesh.load(str(obj_path))
    if isinstance(loaded, trimesh.Scene):
        if len(loaded.geometry) == 0:
            return None
        mesh = trimesh.util.concatenate(list(loaded.geometry.values())) #flatten all sub meshes into one from the obj file
    else:
        mesh = loaded
    bounds = mesh.bounds
    results = []
    for axis, normal in enumerate([[1,0,0],[0,1,0],[0,0,1]]): #goes through all three axes
        zvals = np.linspace(bounds[0,axis], bounds[1,axis], n_slices)
        areas, perimeters, hole_counts = [], [], []
        hull_areas, hull_perimeters = [], []
        centroids_x, centroids_y = [], []
        valid = []  # will be false if the slice is empty
        for z in zvals:
            origin = [0,0,0]; origin[axis] = z
            s = mesh.section(plane_origin=origin, plane_normal=normal)
            if s is None:
                areas.append(0); perimeters.append(0); hole_counts.append(0)
                hull_areas.append(0); hull_perimeters.append(0)
                centroids_x.append(0); centroids_y.append(0)
                valid.append(False)  # mark as empty, will be excluded from scoring
            else:
                try:
                    poly, _ = s.to_2D()
                    areas.append(poly.area)
                    perimeters.append(poly.length)
                    # polygons_full is the correct way to count separate closed regions
                    # (e.g. lego studs) — the old hasattr(poly, 'geoms') check was
                    # always False since to_2D() returns a Path2D not a shapely object,
                    # meaning hole_count was always 1 and topo_change was always 0
                    hole_counts.append(len(poly.polygons_full))
                    merged = None
                    for sub in poly.polygons_full:
                        merged = sub if merged is None else merged.union(sub)
                    if merged is not None:
                        hull = merged.convex_hull
                        hull_areas.append(hull.area)
                        hull_perimeters.append(hull.length)
                        # centroid of the merged polygon — tracks center of mass position
                        centroids_x.append(merged.centroid.x)
                        centroids_y.append(merged.centroid.y)
                    else:
                        hull_areas.append(0); hull_perimeters.append(0)
                        centroids_x.append(0); centroids_y.append(0)
                    valid.append(True)
                except:
                    areas.append(0); perimeters.append(0); hole_counts.append(0)
                    hull_areas.append(0); hull_perimeters.append(0)
                    centroids_x.append(0); centroids_y.append(0)
                    valid.append(False)  # failed to parse, treat as empty
        results.append((areas, perimeters, hole_counts,
                        hull_areas, hull_perimeters,
                        centroids_x, centroids_y,
                        valid))
    return results


def extract_features(areas, perimeters, hole_counts, hull_areas, hull_perimeters, centroids_x, centroids_y):
    #convert raw per slice measurement into normalized ML features
    areas = np.array(areas)
    perimeters = np.array(perimeters)
    holes = np.array(hole_counts)
    hull_areas = np.array(hull_areas)
    hull_perimeters = np.array(hull_perimeters)
    cx = np.array(centroids_x)
    cy = np.array(centroids_y)

    def norm(x): return (x - x.min()) / (x.max() - x.min() + 1e-8)

    # rate of change of area between slices
    area_delta = np.abs(np.gradient(areas))
    # perimeter/area ratio — irregular shapes score higher than simple blobs
    with np.errstate(divide='ignore', invalid='ignore'):
        entropy = np.where(areas > 0, perimeters / (areas + 1e-6), 0)
    # how much the number of separate components changed from the previous slice
    topo_change = np.abs(np.diff(holes, prepend=holes[0])).astype(float)
    # second derivative of area — peaks at inflection points
    area_delta2 = np.abs(np.gradient(area_delta))
    # relative position along axis (0 = start, 1 = end)
    position = np.linspace(0, 1, len(areas))
    # convexity: actual area / convex hull area
    # close to 1.0 = convex shape, much less than 1.0 = concave/complex (e.g. stud row)
    with np.errstate(divide='ignore', invalid='ignore'):
        convexity = np.where(hull_areas > 0, areas / (hull_areas + 1e-6), 1.0)
    # hull perimeter ratio: actual perimeter / convex hull perimeter
    # high = jagged/complex boundary relative to convex envelope
    with np.errstate(divide='ignore', invalid='ignore'):
        hull_perim_ratio = np.where(hull_perimeters > 0,
                                    perimeters / (hull_perimeters + 1e-6), 1.0)
    # centroid drift: how much the center of mass moves between consecutive slices
    # spikes when geometry shifts off-axis (asymmetric parts, holes moving off-center)
    centroid_drift = np.sqrt(np.diff(cx, prepend=cx[0])**2 +
                             np.diff(cy, prepend=cy[0])**2)

    # 10 features per slice
    return np.stack([
        norm(area_delta),
        norm(entropy),
        norm(topo_change),
        norm(areas),
        norm(area_delta2),
        position,
        norm(holes.astype(float)),   # raw component count
        norm(convexity),             # how convex the cross section is
        norm(hull_perim_ratio),      # boundary complexity relative to convex hull
        norm(centroid_drift),        # how much center of mass shifts between slices
    ], axis=1)


def get_geometry_labels(areas, perimeters, hole_counts, hull_areas, hull_perimeters, centroids_x, centroids_y):
    """Hand-tuned importance label combining area change, topology, boundary
    complexity, non-convexity, and centroid drift."""
    areas = np.array(areas)
    holes = np.array(hole_counts)
    perimeters = np.array(perimeters)
    hull_areas = np.array(hull_areas)
    cx = np.array(centroids_x)
    cy = np.array(centroids_y)

    area_delta = np.abs(np.gradient(areas))
    topo_change = np.abs(np.diff(holes, prepend=holes[0])).astype(float)
    with np.errstate(divide='ignore', invalid='ignore'):
        entropy = np.where(areas > 0, perimeters / (areas + 1e-6), 0)
    with np.errstate(divide='ignore', invalid='ignore'):
        convexity = np.where(hull_areas > 0, areas / (hull_areas + 1e-6), 1.0)
    non_convexity = 1.0 - convexity
    centroid_drift = np.sqrt(np.diff(cx, prepend=cx[0])**2 +
                             np.diff(cy, prepend=cy[0])**2)

    def norm(x): return (x - x.min()) / (x.max() - x.min() + 1e-8)

    importance = (1.0 * norm(area_delta) +
                  2.0 * norm(topo_change) +
                  1.0 * norm(entropy) +
                  2.0 * norm(non_convexity) +
                  1.5 * norm(centroid_drift))
    return (importance - importance.min()) / (importance.max() - importance.min() + 1e-8)


#everything below only runs when you execute this file directly
if __name__ == "__main__":

    #build dataset
    X_all, y_all = [], []

    obj_files = list(Path("objs").glob("**/objects/*.obj"))  # flat objects folder
    obj_files += list(Path("objs").glob("*/[0-9]*.obj"))     # numbered subfolders
    obj_files = list(set(obj_files))  # deduplicate in case any overlap
    print(f"Found {len(obj_files)} models")

    for obj_path in tqdm(obj_files):
        try:
            slices = slice_model(obj_path, n_slices=100)
            if slices is None:
                continue
            for (areas, perims, holes,
                 hull_areas, hull_perims,
                 cx, cy, valid) in slices:
                valid = np.array(valid)
                # skip empty slices — they have no geometry and would pollute training
                if valid.sum() == 0:
                    continue
                feats = extract_features(areas, perims, holes,
                                         hull_areas, hull_perims, cx, cy)
                labels = get_geometry_labels(areas, perims, holes,
                                             hull_areas, hull_perims, cx, cy)
                # only keep rows where the slice actually had geometry
                X_all.append(feats[valid])
                y_all.append(labels[valid])
        except Exception as e:
            print(f"skipped {obj_path.name}: {e}")

    if not X_all:
        print("No data collected — check your objs/ folder path")
        exit()

    X = np.vstack(X_all)
    y = np.concatenate(y_all)
    print(f"Dataset: {X.shape[0]} slices from {len(obj_files)} models")

    # --- train ---
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42)

    # random forest: builds 200 trees independently in parallel, final score = average
    model = RandomForestRegressor(
        n_estimators=200,
        max_depth=6,
        n_jobs=-1
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    print(f"MSE: {mean_squared_error(y_test, preds):.4f}")

    feature_names = ['area_delta', 'entropy', 'topo_change', 'areas', 'area_delta2',
                     'position', 'hole_count', 'convexity', 'hull_perim_ratio',
                     'centroid_drift']
    for name, imp in zip(feature_names, model.feature_importances_):
        print(f"  {name}: {imp:.4f}")

    # --- evaluate ---
    def top_k_overlap(pred, true, k=5):
        return len(set(np.argsort(pred)[-k:]) & set(np.argsort(true)[-k:])) / k

    overlaps = []
    correlations = []
    for i in range(0, len(X_test)-100, 100):
        pred = model.predict(X_test[i:i+100])
        true = y_test[i:i+100]
        overlaps.append(top_k_overlap(pred, true))
        corr, _ = spearmanr(pred, true)
        if not np.isnan(corr):
            correlations.append(corr)

    print(f"Mean top-5 overlap: {np.mean(overlaps):.2f}")
    print(f"Mean Spearman correlation: {np.mean(correlations):.2f}")

    joblib.dump(model, "slice_scorer_abc_only_v5.pkl")
    print("Saved slice_scorer_abc_only_v5.pkl")