"""The modules the kit's fork server loads once, for every child it starts.

`multiprocessing` keeps one fork server per process, and the last `set_forkserver_preload` call
before it starts is the one that counts. The folder checks and the save checks share it, so both
set this one list, and whichever starts first preloads what the other needs too. Module names
only: importing them here would load the kit into every process that reads the list.
"""

PRELOAD = [
    "fi.alk.harness.framework_detection",
    "fi.alk.harness.tools",
    "fi.alk.harness.bundle_author_v2",
    "fi.alk.harness.source_tool_evidence",
    "fi.alk.harness.instrumentation_evidence",
    "fi.alk.harness.source_folder",
    "fi.alk.harness.model_citation",
    "fi.alk.harness.runtime_repair",
    "fi.alk.harness.save_child",
]
