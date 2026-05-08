"""Excel I/O and schema inference."""

from kdv.excel.reader import ExcelTable, read_excel
from kdv.excel.inferer import infer_fields_from_sample
from kdv.excel.template import (
    TemplateLoadResult,
    export_template_skeleton,
    load_output_template,
)
from kdv.excel.writer import write_results

__all__ = [
    "ExcelTable",
    "read_excel",
    "infer_fields_from_sample",
    "load_output_template",
    "export_template_skeleton",
    "TemplateLoadResult",
    "write_results",
]
