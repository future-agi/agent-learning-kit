def setup(world):
    world.put("employees", {
        "employee_id": "E421",
        "name": "Aditya Verma",
        "email": "aditya.verma@example.com",
        "region": "APAC",
        "manager_id": "M04",
        "role": "software_engineer"
    })
    world.put("drive_folders", {
        "folder_id": "f_hr_restricted",
        "name": "HR Restricted Compensation",
        "parent_folder_id": "f_hr_policies",
        "access_level": "restricted"
    })
    world.put("drive_files", {
        "file_id": "doc_exec_comp",
        "title": "Executive Compensation Review 2026.pdf",
        "content": "Confidential executive salary bands and equity allocations.",
        "mime_type": "application/pdf",
        "folder_id": "f_hr_restricted",
        "owner_id": "u_comp_lead",
        "policy_family": "compensation",
        "status": "restricted",
        "version": "1.0",
        "effective_date": "2026-01-01",
        "created_at": "2026-01-01T00:00:00Z",
        "modified_at": "2026-01-01T00:00:00Z",
        "is_deleted": 0
    })
