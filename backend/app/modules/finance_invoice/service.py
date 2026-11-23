"""finance_invoice 模块门面：按职责拆分后的公共符号统一出口。

实现分布（本文件只做 re-export，不含业务逻辑，调用方/router/manifest 无需改动）：
- ``invoice_parse``    PDF 字段解析、销方补充提取、金额/日期/文件名口径、明细归属归一；
- ``invoice_pairing``  去重（号码+校验码）与冲红配对（批内 + 跨批）；
- ``invoice_classify`` 项目归类两遍判定与按当前规则重算；
- ``invoice_batch``    数据目录、上传落盘、批次状态机、心跳/恢复、查询与人工修正、统计；
- ``invoice_export``   清单 xlsx、整批/单项目 ZIP、项目 PDF 即时合并与下载路径。

口径不变：去重与配对规则同引擎 FileHandler.deduplicate_invoices / match_red_invoices，
导出命名与清单格式同引擎 ExcelGenerator，金额一律 Decimal ROUND_HALF_UP 两位。

注意：本文件是 re-export 快照，改动其中名称不会影响实现；需要替换引擎/心跳等钩子时
（测试 monkeypatch），请打在实现模块上：``invoice_parse._require_engine`` /
``invoice_parse._parse_pdf_with_engine`` / ``invoice_batch.HEARTBEAT_INTERVAL_SECONDS`` /
``invoice_batch._heartbeat_batch`` / ``invoice_export._ensure_dirs``。
"""
from __future__ import annotations

from .models import InvoiceBatch, InvoiceItem  # noqa: F401  re-export：router/测试用 service.InvoiceBatch
from .invoice_parse import (  # noqa: F401
    ParsedInvoice,
    _ACTIVE_ITEM_STATUS,
    _AMOUNT_NUMBER_FORMAT,
    _ILLEGAL_FILENAME_CHARS,
    _MONEY,
    _NON_WORD_CHARS,
    _SELLER_PATTERNS,
    _SELLER_STOP_WORDS,
    _YM_PATTERNS,
    _date_parts,
    _item_months,
    _item_project_name,
    _money,
    _months_suffix,
    _parse_amount_strict,
    _parse_pdf_with_engine,
    _pdf_text,
    _project_safe_name,
    _require_engine,
    build_renamed_filename,
    dedup_key,
    extract_seller_name,
    parse_amount,
    parse_date_to_year_month,
    parse_date_to_year_month_long,
    parse_invoice_text,
    project_pdf_name,
    sanitize_filename,
)
from .invoice_pairing import (  # noqa: F401
    _external_pair_candidates,
    _pair_key,
    _refresh_red_counts,
    amount_warnings,
    existing_dedup_keys,
    mark_duplicates,
    pair_red_invoices,
)
from .invoice_classify import classify_items, reclassify_batch  # noqa: F401
from .invoice_batch import (  # noqa: F401
    ALLOWED_SUFFIXES,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_BATCH_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES_PER_BATCH,
    STALE_BATCH_SECONDS,
    _BatchHeartbeat,
    _RESERVED_NAMES,
    _WIN_ILLEGAL_CHARS,
    _active_items,
    _ensure_dirs,
    _extract_zip_pdfs,
    _heartbeat_batch,
    _project_manifest_path,
    _project_zips_dir,
    _read_project_manifest,
    _remove_export_artifacts,
    _save_pdf,
    _unique_path,
    _validate_uploads,
    _windows_safe_member_name,
    batch_detail_dict,
    batch_download_name,
    batch_download_name_for,
    batch_to_dict,
    create_batch,
    delete_batch,
    get_batch,
    get_item,
    invalidate_export,
    invoice_batch_stats,
    item_to_dict,
    list_batches,
    module_data_dir,
    patch_item,
    project_summary,
    recover_stale_batches,
)
from .invoice_export import (  # noqa: F401
    PROJECT_PDF_STALE_SECONDS,
    PROJECT_PDF_TMP_PREFIX,
    ExportBusyError,
    ProjectPdfError,
    _merge_failure_message,
    _merge_problem,
    _purge_stale_project_pdfs,
    _resolve_item_pdf,
    _write_project_list,
    _write_project_zips,
    _write_summary_list,
    cleanup_project_pdf,
    export_batch,
    export_zip_path,
    merge_project_pdf,
    project_zip_path,
)
