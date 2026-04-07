from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import httpx


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_archives(zip_paths: list[Path], target_dir: Path) -> list[Path]:
    roots: list[Path] = []
    for idx, z in enumerate(zip_paths, start=1):
        out = target_dir / f"archive_{idx}"
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z, "r") as zf:
            zf.extractall(out)
        roots.append(out)
    return roots


def collect_pdfs(roots: list[Path]) -> list[Path]:
    pdfs: list[Path] = []
    for root in roots:
        pdfs.extend(sorted(root.rglob("*.pdf")))
    return pdfs


def existing_docs_by_sha(client: httpx.Client) -> dict[str, str]:
    out: dict[str, str] = {}
    docs = client.get("/documents?limit=5000").json()
    # Only works for docs uploaded via this backend if sha can be queried later;
    # fallback behavior is skip-on-409.
    for doc in docs:
        # sha not exposed in endpoint, keep placeholder map empty.
        _ = doc
    return out


def run(base_url: str, zip_paths: list[Path], workdir: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    roots = extract_archives(zip_paths, workdir)
    pdfs = collect_pdfs(roots)
    report_items: list[dict] = []

    with httpx.Client(base_url=base_url, timeout=120.0) as client:
        client.get("/health").raise_for_status()
        _ = existing_docs_by_sha(client)

        for idx, pdf in enumerate(pdfs, start=1):
            print(f"[{idx}/{len(pdfs)}] {pdf.name}", flush=True)
            item = {"pdf": str(pdf)}
            try:
                files = {"file": (pdf.name, pdf.read_bytes(), "application/pdf")}
                upload = client.post("/documents", files=files)
                if upload.status_code == 409:
                    item["status"] = "duplicate"
                    report_items.append(item)
                    continue
                upload.raise_for_status()
                up_data = upload.json()
                doc_id = up_data["document_id"]
                item["document_id"] = doc_id
                item["status"] = "uploaded"

                proc = client.post(f"/documents/{doc_id}/process")
                proc.raise_for_status()
                proc_data = proc.json()
                item["process"] = proc_data
                item["status"] = "processed"
            except Exception as exc:  # noqa: BLE001
                item["status"] = "error"
                item["error"] = str(exc)
            report_items.append(item)

    summary = {
        "total_pdfs": len(pdfs),
        "processed": sum(1 for i in report_items if i["status"] == "processed"),
        "duplicates": sum(1 for i in report_items if i["status"] == "duplicate"),
        "errors": sum(1 for i in report_items if i["status"] == "error"),
    }
    return {"summary": summary, "items": report_items}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest PDF archives into local Examable UI/backend.")
    parser.add_argument("zips", nargs="+", type=Path)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--workdir", type=Path, default=Path("ingest_runs"))
    parser.add_argument("--report", type=Path, default=Path("ingest_report.json"))
    args = parser.parse_args()

    for z in args.zips:
        if not z.exists():
            raise SystemExit(f"Archive not found: {z}")

    report = run(args.base_url, args.zips, args.workdir / "latest")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"Report saved: {args.report}")


if __name__ == "__main__":
    main()
