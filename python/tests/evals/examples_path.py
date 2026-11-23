"""Load python/examples/feedback_torchembed_retrieval_demo.py as a module for tests."""
import importlib.util
from pathlib import Path


def load_demo():
    path = Path(__file__).resolve().parents[2] / "examples" / "feedback_torchembed_retrieval_demo.py"
    spec = importlib.util.spec_from_file_location("feedback_torchembed_retrieval_demo", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
