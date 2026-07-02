import sys
from pathlib import Path
import numpy as np
import trimesh
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.cluster import KMeans

# import shared functions directly from train_abc.py so they never get out of sync
# train_abc.py must be in the same directory as this script
sys.path.insert(0, str(Path(__file__).parent))
from train_abc import extract_features


def slice_model_with_polygons(mesh_path, n_slices=100):
    #loads stl file and cuts into n slices, kind of the same as slice_model in train_abc
    loaded = trimesh.load(str(mesh_path))
    if isinstance(loaded, trimesh.Scene):
        if len(loaded.geometry) == 0:
            return None
        mesh = trimesh.util.concatenate(list(loaded.geometry.values()))
    else:
        mesh = loaded
    bounds = mesh.bounds
    results = []
    for axis, normal in enumerate([[1, 0, 0], [0, 1, 0], [0, 0, 1]]):
        zvals = np.linspace(bounds[0, axis], bounds[1, axis], n_slices)
        areas, perimeters, hole_counts = [], [], []
        hull_areas, hull_perimeters = [], []
        centroids_x, centroids_y = [], []
        polygons, valid = [], []
        for z in zvals:
            origin = [0, 0, 0]
            origin[axis] = z
            s = mesh.section(plane_origin=origin, plane_normal=normal)
            if s is None:
                areas.append(0); perimeters.append(0); hole_counts.append(0)
                hull_areas.append(0); hull_perimeters.append(0)
                centroids_x.append(0); centroids_y.append(0)
                polygons.append(None); valid.append(False)
            else:
                try:
                    poly, _ = s.to_2D()
                    areas.append(poly.area)
                    perimeters.append(poly.length)
                    hole_counts.append(len(poly.polygons_full))
                    polygons.append(poly)
                    merged = None
                    for sub in poly.polygons_full:
                        merged = sub if merged is None else merged.union(sub)
                    if merged is not None:
                        hull = merged.convex_hull
                        hull_areas.append(hull.area)
                        hull_perimeters.append(hull.length)
                        centroids_x.append(merged.centroid.x)
                        centroids_y.append(merged.centroid.y)
                    else:
                        hull_areas.append(0); hull_perimeters.append(0)
                        centroids_x.append(0); centroids_y.append(0)
                    valid.append(True)
                except Exception:
                    areas.append(0); perimeters.append(0); hole_counts.append(0)
                    hull_areas.append(0); hull_perimeters.append(0)
                    centroids_x.append(0); centroids_y.append(0)
                    polygons.append(None); valid.append(False)
        results.append((areas, perimeters, hole_counts,
                        hull_areas, hull_perimeters,
                        centroids_x, centroids_y,
                        polygons, valid))
    return results


def diverse_top_k(feats, scores, valid_indices, top_k):
    #using k means to cluster the slices based on feature similarity, picks top slice from each cluster
    n_valid = len(valid_indices)

    # if fewer valid slices than top_k, just return all of them ranked by score
    if n_valid <= top_k:
        ranked = np.argsort(scores[valid_indices])[::-1]
        return valid_indices[ranked]

    # cluster valid slices into top_k groups by feature similarity
    valid_feats = feats[valid_indices]
    n_clusters = min(top_k, n_valid)
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    cluster_labels = kmeans.fit_predict(valid_feats)

    # from each cluster pick the slice with the highest score
    selected = []
    for cluster_id in range(n_clusters):
        cluster_mask = cluster_labels == cluster_id
        cluster_indices = valid_indices[cluster_mask]
        cluster_scores = scores[cluster_indices]
        best_in_cluster = cluster_indices[np.argmax(cluster_scores)]
        selected.append(best_in_cluster)

    # sort selected by position in object so you can see how the object changes in the axis
    selected = np.array(selected)
    selected = selected[np.argsort(selected)]
    return selected


def plot_cross_sections(axis_name, areas, holes, polygons, scores,
                        slice_indices, out_path, top_k):
    #generate the output image
    n_slices = len(areas)

    n_cols = min(top_k, 5)
    n_rows = int(np.ceil(len(slice_indices) / n_cols)) + 1

    fig = plt.figure(figsize=(3 * n_cols, 3 * n_rows))

    ax_score = plt.subplot(n_rows, 1, 1)
    # only plot scores for non-empty slices
    valid_mask = scores >= 0
    valid_x = np.where(valid_mask)[0]
    ax_score.plot(valid_x, scores[valid_mask], color="steelblue",
                  linewidth=1.5)
    ax_score.scatter(slice_indices, scores[slice_indices],
                     color="crimson", zorder=5, label=f"top {top_k} (diverse)")
    ax_score.set_title(f"Axis {axis_name}: predicted importance per slice")
    ax_score.set_xlabel("slice index")
    ax_score.set_ylabel("score")
    ax_score.legend(loc="upper right", fontsize=8)

    for rank_pos, idx in enumerate(slice_indices):
        ax = plt.subplot(n_rows, n_cols, n_cols + rank_pos + 1)
        poly = polygons[idx]
        if poly is not None:
            try:
                for sub in poly.polygons_full:
                    xs, ys = sub.exterior.xy
                    ax.fill(xs, ys, color="darkorange", alpha=0.6,
                            edgecolor="black", linewidth=1)
                    for interior in sub.interiors:
                        ixs, iys = interior.xy
                        ax.fill(ixs, iys, color="white",
                                edgecolor="black", linewidth=0.8)
            except Exception:
                ax.text(0.5, 0.5, "shape\nunavailable",
                        ha="center", va="center", fontsize=8)
        else:
            ax.text(0.5, 0.5, "empty\nslice",
                    ha="center", va="center", fontsize=8)

        ax.set_title(f"slice {idx}\nscore={scores[idx]:.3f}  holes={holes[idx]}",
                     fontsize=9)
        ax.set_aspect("equal")
        ax.set_xticks([]); ax.set_yticks([])

    plt.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved visualization: {out_path}")


def main():
    if len(sys.argv) < 3:
        print("Usage: python score_single_model.py <path_to_stl_or_obj> "
              "<path_to_model.pkl> [n_slices] [top_k]")
        print("  n_slices: must match what the model was trained with (default 100)")
        print("  top_k:    number of diverse top slices to show (default 5)")
        sys.exit(1)

    mesh_path = sys.argv[1]
    model_path = sys.argv[2]
    n_slices = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    top_k    = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    model = joblib.load(model_path)
    stem = Path(mesh_path).stem

    slices = slice_model_with_polygons(mesh_path, n_slices=n_slices)
    if slices is None:
        print("Could not load/slice mesh (empty scene or failed load).")
        sys.exit(1)

    axis_names = ['X', 'Y', 'Z']

    for axis_idx, (areas, perims, holes,
                   hull_areas, hull_perims,
                   cx, cy, polygons, valid) in enumerate(slices):

        valid = np.array(valid)
        valid_indices = np.where(valid)[0]

        if len(valid_indices) == 0:
            print(f"\n=== Axis {axis_names[axis_idx]} === (all slices empty, skipping)")
            continue

        feats = extract_features(areas, perims, holes,
                                 hull_areas, hull_perims, cx, cy)

        # scores for all slices; empty ones get -1 so they never rank
        all_scores = np.full(n_slices, -1.0)
        all_scores[valid] = model.predict(feats[valid])

        # pick diverse top-k: one representative per structural cluster
        top_indices = diverse_top_k(feats, all_scores, valid_indices, top_k)

        print(f"\n=== Axis {axis_names[axis_idx]} ===")
        print(f"Top {top_k} diverse slice indices by predicted importance:")
        for idx in top_indices:
            print(f"  slice {idx:2d}  score={all_scores[idx]:.4f}  "
                  f"area={areas[idx]:.4f}  perimeter={perims[idx]:.4f}  "
                  f"holes={holes[idx]}")

        out_path = f"{stem}_axis_{axis_names[axis_idx]}_slices.png"
        plot_cross_sections(axis_names[axis_idx],
                            np.array(areas), np.array(holes),
                            polygons, all_scores,
                            top_indices, out_path, top_k)


if __name__ == "__main__":
    main()