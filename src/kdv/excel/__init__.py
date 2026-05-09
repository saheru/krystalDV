"""Excel I/O and schema inference."""

from kdv.excel.reader import (
    ExcelTable,
    TABLE_ID_COLUMN,
    concat_tables,
    make_table_id,
    read_excel,
    read_workbook,
)
from kdv.excel.inferer import infer_fields_from_sample
from kdv.excel.template import (
    TemplateLoadResult,
    export_template_skeleton,
    load_output_template,
)
from kdv.excel.writer import write_results

__all__ = [
    "ExcelTable",
    "TABLE_ID_COLUMN",
    "concat_tables",
    "make_table_id",
    "read_excel",
    "read_workbook",
    "infer_fields_from_sample",
    "load_output_template",
    "export_template_skeleton",
    "TemplateLoadResult",
    "write_results",
]
