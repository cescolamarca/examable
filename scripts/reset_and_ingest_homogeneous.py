from __future__ import annotations

import json
import uuid
import urllib.request
from pathlib import Path

BASE_URL = "http://localhost:8000"
PARTS = [
    Path(r"c:\Users\nextc\Examable\uploads\homogeneous_parts\intercorso_2021_traccia_A.pdf"),
    Path(r"c:\Users\nextc\Examable\uploads\homogeneous_parts\intercorso_2021_traccia_B.pdf"),
]
OUT_PATH = Path(r"c:\Users\nextc\Examable\homogeneous_ingest_results.json")


def post_json(url: str, payload: dict | None = None, timeout: int = 300) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_pdf(path: Path, timeout: int = 300) -> dict:
    boundary = "----ExamableBoundary" + uuid.uuid4().hex
    file_bytes = path.read_bytes()
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + file_bytes + tail
    req = urllib.request.Request(
        BASE_URL + "/documents",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    reset_result = post_json(BASE_URL + "/admin/reset-db")
    print(reset_result)
    results: list[dict] = []
    for part in PARTS:
        uploaded = upload_pdf(part)
        processed = post_json(BASE_URL + f"/documents/{uploaded['document_id']}/process")
        one = {"file": part.name, "upload": uploaded, "process": processed}
        results.append(one)
        print(
            part.name,
            "doc",
            uploaded["document_id"],
            "extracted",
            processed.get("extracted"),
            "multimodal_used",
            processed.get("multimodal_used"),
            "multimodal_updates",
            processed.get("multimodal_updates"),
        )
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", str(OUT_PATH))


if __name__ == "__main__":
    main()
