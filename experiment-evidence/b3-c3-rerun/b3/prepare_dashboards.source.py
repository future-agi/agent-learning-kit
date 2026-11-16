"""Evidence-only: embeds original results plus verbatim harness source for artifact-kit."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = (ROOT / "run_experiments.py").read_text()
tree = ast.parse(source)
for name in ("b3", "c3"):
    result = json.loads((ROOT / name / "result.json").read_text())
    wanted = {"fixture", "pdf", name}
    result["harness_source"] = "\n\n".join(
        ast.get_source_segment(source, node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    )
    (ROOT / name / "dashboard-data.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
