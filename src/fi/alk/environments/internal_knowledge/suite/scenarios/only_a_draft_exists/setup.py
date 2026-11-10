def setup(world):
    world.put("drive_files", {
        "file_id": "ai_tools_v1_draft",
        "title": "AI Tools Use Draft.md",
        "folder_id": "f_it",
        "owner_id": "U_E07",
        "mime_type": "text/markdown",
        "created_at": "2026-10-01T00:00:00Z",
        "modified_at": "2026-10-01T00:00:00Z",
        "policy_family": "ai_tools",
        "version": "1.0",
        "status": "draft",
        "effective_date": "2027-01-01",
        "content": "# AI Tools Use Draft\nStatus: Draft. Version 1.0. Effective: 2027-01-01.\n\n## 1. Internal Data\n[P-AITOOLSV1DRAFT-01] Proposed: employees may paste internal data into approved AI assistants. Not approved.",
        "is_deleted": 0
    })
    world.put("kb_documents", {
        "drive_file_id": "ai_tools_v1_draft",
        "title": "AI Tools Use Draft.md",
        "collection_id": "C_IT",
        "owner_id": "E07",
        "mime_type": "text/markdown",
        "family": "ai_tools",
        "version": "1.0",
        "status": "draft",
        "effective_date": "2027-01-01"
    })
    world.put("kb_passages", {
        "passage_id": "P-AITOOLSV1DRAFT-01",
        "drive_file_id": "ai_tools_v1_draft",
        "section": 1,
        "heading": "Internal Data",
        "text": "Proposed: employees may paste internal data into approved AI assistants. Not approved."
    })