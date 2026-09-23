"""Office tool - Excel, Word, PDF, CSV, and SQLite operations."""

from typing import Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics

from windows_mcp.office import (
    csv_read as csv_read_service,
    csv_write as csv_write_service,
    excel_add_sheet as excel_add_sheet_service,
    excel_append as excel_append_service,
    excel_create as excel_create_service,
    excel_delete_sheet as excel_delete_sheet_service,
    excel_iterate_rows as excel_iterate_rows_service,
    excel_list_sheets as excel_list_sheets_service,
    excel_read as excel_read_service,
    excel_save as excel_save_service,
    excel_set_formula as excel_set_formula_service,
    excel_write as excel_write_service,
    pdf_extract_pages as pdf_extract_pages_service,
    pdf_extract_text as pdf_extract_text_service,
    pdf_info as pdf_info_service,
    pdf_merge as pdf_merge_service,
    pdf_split as pdf_split_service,
    sqlite_batch_insert as sqlite_batch_insert_service,
    sqlite_create as sqlite_create_service,
    sqlite_execute as sqlite_execute_service,
    sqlite_query as sqlite_query_service,
    sqlite_tables as sqlite_tables_service,
    sqlite_transaction as sqlite_transaction_service,
    word_add_heading as word_add_heading_service,
    word_add_picture as word_add_picture_service,
    word_create as word_create_service,
    word_read as word_read_service,
    word_read_tables as word_read_tables_service,
    word_write as word_write_service,
)


def _bool(value: Any, default: bool = False) -> bool:
    """Normalize MCP boolean strings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Office",
        description=(
            "Office document and database operations. Keywords: Excel xlsx, openpyxl, cell, "
            "worksheet, formula, Word docx, paragraph, table, PDF, merge, split, extract text, "
            "CSV, encoding, delimiter, SQLite, SQL, transaction, batch insert. Modes: excel_*, "
            "word_*, pdf_*, csv_*, sqlite_*. Use SQL parameters instead of interpolating values "
            "into sqlite SQL."
        ),
        annotations=ToolAnnotations(
            title="Office",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Office-Tool")
    def office_tool(
        mode: Literal[
            "excel_create",
            "excel_read",
            "excel_write",
            "excel_append",
            "excel_list_sheets",
            "excel_add_sheet",
            "excel_delete_sheet",
            "excel_set_formula",
            "excel_iterate_rows",
            "excel_save",
            "word_create",
            "word_read",
            "word_write",
            "word_read_tables",
            "word_add_heading",
            "word_add_picture",
            "pdf_info",
            "pdf_extract_text",
            "pdf_split",
            "pdf_merge",
            "pdf_extract_pages",
            "csv_read",
            "csv_write",
            "sqlite_create",
            "sqlite_execute",
            "sqlite_query",
            "sqlite_batch_insert",
            "sqlite_transaction",
            "sqlite_tables",
        ],
        path: str | None = None,
        destination: str | None = None,
        sheet: str | None = None,
        cell_range: str | None = None,
        values: Any = None,
        cell: str | None = None,
        formula: str | None = None,
        text: str | None = None,
        style: str | None = None,
        append: bool | str = True,
        level: int | str = 1,
        image_path: str | None = None,
        width_inches: float | str | None = None,
        pages: Any = None,
        sources: str | list[str] | None = None,
        delimiter: str | None = None,
        encoding: str | None = None,
        has_header: bool | str = True,
        max_rows: int | str = 1000,
        sql: str | None = None,
        parameters: Any = None,
        table: str | None = None,
        columns: str | list[str] | None = None,
        rows: Any = None,
        statements: Any = None,
        create: bool | str = False,
        overwrite: bool | str = False,
        data_only: bool | str = True,
        min_row: int | str = 1,
        max_row: int | str | None = None,
        values_only: bool | str = True,
        limit: int | str | None = None,
        table_index: int | str | None = None,
        password: str | None = None,
        max_chars: int | str = 200_000,
        ctx: Context = None,
    ) -> str:
        """Dispatch an Office/database operation and return text or an Error string.

        SQLite execution is parameterized: pass untrusted values through parameters, for example
        sql="INSERT INTO t(name) VALUES (?)" and parameters=["Alice"]. Never concatenate
        untrusted text into SQL. Excel only supports .xlsx/.xlsm; Word only supports .docx.
        """
        try:
            if mode == "excel_create":
                if not path:
                    return "Error: path is required for excel_create."
                return excel_create_service(
                    path,
                    sheet_name=sheet or "Sheet",
                    overwrite=_bool(overwrite),
                )
            if mode == "excel_read":
                if not path:
                    return "Error: path is required for excel_read."
                return excel_read_service(
                    path,
                    sheet=sheet,
                    cell_range=cell_range,
                    data_only=_bool(data_only, True),
                )
            if mode == "excel_write":
                if not path or values is None:
                    return "Error: path and values are required for excel_write."
                return excel_write_service(
                    path,
                    values=values,
                    sheet=sheet,
                    cell_range=cell_range,
                )
            if mode == "excel_append":
                if not path or values is None:
                    return "Error: path and values are required for excel_append."
                return excel_append_service(path, values=values, sheet=sheet)
            if mode == "excel_list_sheets":
                return excel_list_sheets_service(_require_path(path, mode))
            if mode == "excel_add_sheet":
                if not sheet:
                    return "Error: sheet is required for excel_add_sheet."
                return excel_add_sheet_service(_require_path(path, mode), sheet)
            if mode == "excel_delete_sheet":
                if not sheet:
                    return "Error: sheet is required for excel_delete_sheet."
                return excel_delete_sheet_service(_require_path(path, mode), sheet)
            if mode == "excel_set_formula":
                if not path or not cell or formula is None:
                    return "Error: path, cell, and formula are required for excel_set_formula."
                return excel_set_formula_service(path, cell, formula, sheet=sheet)
            if mode == "excel_iterate_rows":
                return excel_iterate_rows_service(
                    _require_path(path, mode),
                    sheet=sheet,
                    min_row=int(min_row),
                    max_row=int(max_row) if max_row not in (None, "") else None,
                    values_only=_bool(values_only, True),
                    limit=int(limit or 1000),
                )
            if mode == "excel_save":
                return excel_save_service(
                    _require_path(path, mode),
                    destination=destination,
                    overwrite=_bool(overwrite),
                )

            if mode == "word_create":
                return word_create_service(_require_path(path, mode), overwrite=_bool(overwrite))
            if mode == "word_read":
                return word_read_service(_require_path(path, mode))
            if mode == "word_write":
                if not path or text is None:
                    return "Error: path and text are required for word_write."
                return word_write_service(
                    path,
                    text,
                    append=_bool(append, True),
                    style=style,
                    create_if_missing=True,
                )
            if mode == "word_read_tables":
                return word_read_tables_service(
                    _require_path(path, mode),
                    table_index=int(table_index) if table_index not in (None, "") else None,
                )
            if mode == "word_add_heading":
                if not path or text is None:
                    return "Error: path and text are required for word_add_heading."
                return word_add_heading_service(path, text, level=int(level))
            if mode == "word_add_picture":
                if not path or not image_path:
                    return "Error: path and image_path are required for word_add_picture."
                return word_add_picture_service(
                    path,
                    image_path,
                    width_inches=(float(width_inches) if width_inches not in (None, "") else None),
                )

            if mode == "pdf_info":
                return pdf_info_service(_require_path(path, mode), password=password)
            if mode == "pdf_extract_text":
                return pdf_extract_text_service(
                    _require_path(path, mode),
                    pages=pages,
                    password=password,
                    max_chars=int(max_chars),
                )
            if mode == "pdf_merge":
                if not sources or not destination:
                    return "Error: sources and destination are required for pdf_merge."
                return pdf_merge_service(
                    sources,
                    destination,
                    overwrite=_bool(overwrite),
                    password=password,
                )
            if mode == "pdf_extract_pages":
                if not path or not destination or pages in (None, "", []):
                    return "Error: path, destination, and pages are required for pdf_extract_pages."
                return pdf_extract_pages_service(
                    path,
                    destination,
                    pages,
                    overwrite=_bool(overwrite),
                    password=password,
                )
            if mode == "pdf_split":
                if not path or not destination:
                    return "Error: path and destination are required for pdf_split."
                return pdf_split_service(
                    path,
                    destination,
                    overwrite=_bool(overwrite),
                    password=password,
                )

            if mode == "csv_read":
                return csv_read_service(
                    _require_path(path, mode),
                    delimiter=delimiter,
                    encoding=encoding,
                    has_header=_bool(has_header, True),
                    max_rows=int(max_rows),
                )
            if mode == "csv_write":
                if not path or rows is None:
                    return "Error: path and rows are required for csv_write."
                return csv_write_service(
                    path,
                    rows=rows,
                    delimiter=delimiter or ",",
                    encoding=encoding or "utf-8-sig",
                    overwrite=_bool(overwrite),
                )

            if mode == "sqlite_create":
                return sqlite_create_service(_require_path(path, mode), overwrite=_bool(overwrite))
            if mode == "sqlite_execute":
                if not path or not sql:
                    return "Error: path and sql are required for sqlite_execute."
                return sqlite_execute_service(
                    path,
                    sql,
                    parameters=parameters,
                    create=_bool(create),
                )
            if mode == "sqlite_query":
                if not path or not sql:
                    return "Error: path and sql are required for sqlite_query."
                return sqlite_query_service(
                    path,
                    sql,
                    parameters=parameters,
                    limit=int(limit) if limit not in (None, "") else None,
                )
            if mode == "sqlite_batch_insert":
                if not path or not table or not columns or rows is None:
                    return (
                        "Error: path, table, columns, and rows are required for "
                        "sqlite_batch_insert."
                    )
                return sqlite_batch_insert_service(
                    path,
                    table,
                    columns,
                    rows,
                    create=_bool(create),
                )
            if mode == "sqlite_transaction":
                if not path or not statements:
                    return "Error: path and statements are required for sqlite_transaction."
                return sqlite_transaction_service(
                    path,
                    statements,
                    create=_bool(create),
                )
            if mode == "sqlite_tables":
                return sqlite_tables_service(_require_path(path, mode))

            return f"Error: Unknown Office mode: {mode}"
        except Exception as exc:
            return f"Error: Office operation failed: {exc}"


def _require_path(path: str | None, mode: str) -> str:
    """Return a required path or raise a consistent error."""
    if not path:
        raise ValueError(f"path is required for {mode}")
    return path
