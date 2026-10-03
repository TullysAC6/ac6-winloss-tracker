"""Offline analysis: python scripts/replay_candidate_bundle.py BUNDLE [--templates PATH]."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from candidate_diagnostics import replay_bundle
from result_detector import ResultClassifier

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--templates', type=Path, default=ROOT / 'detector_templates.json')
    args = parser.parse_args()
    print(json.dumps(replay_bundle(args.bundle, ResultClassifier(args.templates)), indent=2, allow_nan=False))

if __name__ == '__main__':
    main()
