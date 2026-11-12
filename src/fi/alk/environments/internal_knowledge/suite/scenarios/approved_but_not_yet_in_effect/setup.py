def setup(world):
    world.change("session_caller", "default", {"employee_id": "E18"}, by="id")
    world.put("drive_files", {
        "file_id": "per_diem_v3",
        "title": "Meal Per Diem Standard 2027.md",
        "folder_id": "f_travel",
        "owner_id": "U_E04",
        "mime_type": "text/markdown",
        "created_at": "2026-10-01T00:00:00Z",
        "modified_at": "2026-10-01T00:00:00Z",
        "policy_family": "meal_per_diem",
        "version": "3.0",
        "status": "approved",
        "effective_date": "2027-01-01",
        "content": "# Meal Per Diem Standard 2027\nStatus: Approved. Version 3.0. Effective: 2027-01-01.\n\n## 1. Rates\n[P-PERDIEMV3-01] $85 a day in the US and $120 internationally.",
        "is_deleted": 0
    })
    world.put("kb_documents", {
        "drive_file_id": "per_diem_v3",
        "title": "Meal Per Diem Standard 2027.md",
        "collection_id": "C_TRAVEL",
        "owner_id": "E04",
        "mime_type": "text/markdown",
        "family": "meal_per_diem",
        "version": "3.0",
        "status": "approved",
        "effective_date": "2027-01-01"
    })
    world.put("kb_passages", {
        "passage_id": "P-PERDIEMV3-01",
        "drive_file_id": "per_diem_v3",
        "section": 1,
        "heading": "Rates",
        "text": "$85 a day in the US and $120 internationally."
    })