# -*- coding: utf-8 -*-
"""验证 config 分类器两级配置。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import load_config


def main():
    c = load_config()
    print("classifiers:")
    for x in c.classifiers:
        print(f"  {x['name']}: local={x.get('local')} timeout={x.get('timeout')} model={x['model']}")


if __name__ == "__main__":
    main()
