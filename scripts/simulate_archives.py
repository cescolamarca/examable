from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.multimodal import enhance_with_multimodal
from app.parser import extract_text_pages_with_fallback, parse_unisa_questions


def extract_archives(zip_paths: list[Path], target_dir: Path) -> list[Path]:
    extracted_roots: list[Path] = []
    for idx, zip_path in enumerate(zip_paths, start=1):
        out = target_dir / f"archive_{idx}"
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(out)
        extracted_roots.append(out)
    return extracted_roots


def collect_pdfs(roots: list[Path]) -> list[Path]:
    pdfs: list[Path] = []
    for root in roots:
        pdfs.extend(sorted(root.rglob("*.pdf")))
    return pdfs


def _process_one_pdf_worker(pdf_path_str: str, result_queue: mp.Queue) -> None:
    pdf_path = Path(pdf_path_str)
    document_id = uuid4()
    try:
        extraction = extract_text_pages_with_fallback(pdf_path)
        baseline = parse_unisa_questions(document_id=document_id, pages=extraction.pages)
        enhanced = enhance_with_multimodal(
            pdf_path=pdf_path,
            document_id=document_id,
            pages=extraction.pages,
            questions=baseline,
            extraction_quality=extraction.quality_score,
        )
        result_queue.put(
            {
                "pdf": str(pdf_path),
                "extraction_method": extraction.method,
                "extraction_quality": extraction.quality_score,
                "extraction_warnings": extraction.warnings,
                "baseline_questions": len(baseline),
                "final_questions": len(enhanced.questions),
                "multimodal_used": enhanced.used,
                "multimodal_updates": enhanced.updated_items,
                "multimodal_warnings": enhanced.warnings,
            }
        )
    except Exception as exc:  # noqa: BLE001
        result_queue.put({"pdf": str(pdf_path), "error": str(exc)})


def _process_one_pdf_with_timeout(pdf_path: Path, timeout_s: int = 45) -> dict:
    queue: mp.Queue = mp.Queue()
    process = mp.Process(target=_process_one_pdf_worker, args=(str(pdf_path), queue))
    process.start()
    process.join(timeout=timeout_s)

    if process.is_alive():
        process.terminate()
        process.join()
        return {"pdf": str(pdf_path), "error": f"timeout after {timeout_s}s"}

    if not queue.empty():
        return queue.get()
    return {"pdf": str(pdf_path), "error": "empty result from worker"}


def run_simulation(pdfs: list[Path], checkpoint_path: Path | None = None) -> dict:
    items: list[dict] = []
    for idx, pdf_path in enumerate(pdfs, start=1):
        print(f"[{idx}/{len(pdfs)}] Processing {pdf_path.name}", flush=True)
        items.append(_process_one_pdf_with_timeout(pdf_path))

        if checkpoint_path is not None:
            checkpoint = {"processed": len(items), "total": len(pdfs), "items": items}
            checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")

    processed = [i for i in items if "error" not in i]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_pdfs": len(pdfs),
        "processed_ok": len(processed),
        "processed_failed": len(pdfs) - len(processed),
        "multimodal_used_count": sum(1 for i in processed if i["multimodal_used"]),
        "avg_extraction_quality": (
            round(sum(i["extraction_quality"] for i in processed) / len(processed), 4)
            if processed
            else 0.0
        ),
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run batch simulation on PDF archives.")
    parser.add_argument("zips", nargs="+", type=Path, help="Paths to ZIP archives")
    parser.add_argument("--workdir", type=Path, default=Path("simulation_runs"), help="Working folder")
    parser.add_argument("--report", type=Path, default=Path("simulation_report.json"), help="Output report path")
    args = parser.parse_args()

    for zip_path in args.zips:
        if not zip_path.exists():
            raise SystemExit(f"Archive not found: {zip_path}")

    run_dir = args.workdir / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    extracted_roots = extract_archives(args.zips, run_dir)
    pdfs = collect_pdfs(extracted_roots)
    report = run_simulation(pdfs, checkpoint_path=run_dir / "checkpoint.json")

    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Simulation completed: total={report['total_pdfs']} ok={report['processed_ok']} "
        f"failed={report['processed_failed']} multimodal={report['multimodal_used_count']}"
    )
    print(f"Report saved to {args.report}")


if __name__ == "__main__":
    main()
