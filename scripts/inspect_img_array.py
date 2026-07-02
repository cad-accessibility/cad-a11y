#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.cad_comparison_lib import CADComparisonRenderer


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a rendered CAD image array")
    parser.add_argument("--before", default="src/models/brep/cup.step")
    parser.add_argument("--after", default="src/models/brep/cup.step")
    parser.add_argument("--view", default="top")
    parser.add_argument("--depth", type=int, default=0)
    parser.add_argument("--render-mode", default="filled")
    parser.add_argument("--mode", default="single")
    parser.add_argument("--shape", default="after")
    parser.add_argument("--row", type=int, default=0)
    parser.add_argument("--col", type=int, default=0)
    parser.add_argument("--save", default="")
    args = parser.parse_args()

    before_path = str(ROOT / args.before) if not os.path.isabs(args.before) else args.before
    after_path = str(ROOT / args.after) if not os.path.isabs(args.after) else args.after

    renderer = CADComparisonRenderer(before_path, after_path)
    params = {
        "view": args.view,
        "depth": args.depth,
        "renderMode": args.render_mode,
        "mode": args.mode,
        "shape": args.shape,
    }
    img_array = renderer.render(params)

    print("type:", type(img_array).__name__)
    print("shape:", img_array.shape)
    print("dtype:", img_array.dtype)
    print("pixel(0,0):", img_array[0, 0].tolist())
    print("pixel(row,col):", img_array[args.row, args.col].tolist())
    print("min/max:", float(img_array.min()), float(img_array.max()))
    print("alpha nonzero count:", int(np.count_nonzero(img_array[..., 3] > 0)))
    print("alpha unique:", np.unique(img_array[..., 3]).tolist())

    if args.save:
        out_path = Path(args.save)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img_array).save(out_path)
        print("saved:", out_path)


if __name__ == "__main__":
    main()
