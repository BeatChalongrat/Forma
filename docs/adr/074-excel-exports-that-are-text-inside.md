# ADR-074: "Excel" exports that are text inside are read

Status: implemented, 2026-11-20; tested on files written to imitate each export, not on files from a customer

## Context

aCommerce's integration directory names SAP, NetSuite and Oracle. Which client uses which system is not public
(`manual_test_files/acommerce_global_v1/RESEARCH.md`). `backend/tests/test_erp_export_formats.py` imitates
what each system writes when a user exports a list. Five of eleven files were refused at upload:

| System | Export | What the file is |
|---|---|---|
| NetSuite | Saved search, "Export - Microsoft Excel" | XML Spreadsheet 2003 named `.xls` |
| SAP | ALV, Spreadsheet, "Excel (in MHTML format)" | an HTML table in a MIME web archive, `.mhtml` or `.xls` |
| SAP | List, Local File, "Text with Tabs", saved as `.xls` | tab-separated text |
| Oracle OTBI | Export, "Web Archive (.mht)" | an HTML table in a MIME web archive |

FORMA accepted `.xls` only as an OLE workbook. The refusal was clear and nothing wrong was read. But NetSuite's
help says its Excel export writes XLS only, so the default button of one of the three systems could not be used.

## Decision

- `ingestion/markup.py` reads XML Spreadsheet 2003 and HTML tables, alone or in a MIME web archive, into the
  same raw string grid as the other readers. Table detection, mapping and the engine are unchanged.
- The bytes decide. A `.xls` that is XML Spreadsheet or HTML is an Excel source with sheets. A `.xls` that is
  tab-separated text is a delimited-text source. `.mhtml` and `.mht` are new upload types.
- Only `.xls` is widened. Text named `.xlsx`, `.xlsm` or `.xlsb` is refused as before: no export writes that.
  Text named `.xls` with no tab-separated lines is refused.
- XML Spreadsheet: sheet names, `ss:Index` gaps, merged ranges, hidden sheets, rows and columns, formulas and
  error cells are read. An error cell or a formula without a value is refused in a mapped column, as in `.xlsx`.
  A date at midnight is written as the date.
- HTML: each table that holds no other table is a sheet (`Sheet1`, `Sheet2`). `colspan` and `rowspan` place
  cells; the value sits in the first cell. A cell is the text shown, so a number is in the user's format.
- Nothing is executed, fetched or rendered. An XML file that declares a DTD or an entity is refused at upload
  and again by the parser, before any entity is expanded. Scripts and styles in HTML are skipped.
- These exports are limited to 64 MB. A larger one is refused with the advice to export CSV or XLSX.

## Consequences

- The five files are read. The same SAP list gives the same records as XLSX, as text, as text named `.xls` and
  as MHTML.
- A markup export does not carry the "legacy Excel, verify" warning of an OLE `.xls`. Formulas and hidden
  sheets, rows and columns in XML Spreadsheet give the same notices as `.xlsx`.
- Cell positions shown in lineage are relative to the used range for these files, as for OLE `.xls`.

## What this does not establish

- The files imitate the documented export mechanisms. No file exported from a real SAP, NetSuite or Oracle
  system has been read.
- `.htm` and `.html` (SAP's "HTML Format") are not upload types.
- Three findings of the same test file were open when this was written: the dashed rules of SAP's
  "Unconverted" list, NetSuite's `Internal ID` taken for the product code, and the values Oracle OTBI
  suppresses down merged cells. ADR-075 closes them.

## Sources

- NetSuite, Exporting Search Results: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_N663983.html
- NetSuite community, Excel export is XML with every cell a string: https://community.oracle.com/netsuite/english/discussion/4255454/exporting-search-to-excel-xml-all-cells-are-strings
- SAP KBA 2193144 and 3413729, "Text with Tabs" saved as `.xls`: https://userapps.support.sap.com/sap/support/knowledge/en/2193144
- SAP Help, saving a list to a local file: https://help.sap.com/saphelp_ewm900/helpdata/en/4d/6204f5d79751b0e10000000a42189c/content.htm
- SAP Help, ALV spreadsheet formats: https://help.sap.com/doc/saphelp_nwpi71/7.1/en-US/45/6e983a53ad487fe10000000a114a6b/content.htm
- Oracle, exporting analysis data: https://docs.oracle.com/en/cloud/saas/taleo-enterprise/otbou/t-exportingareportkpioranalysispromptdata.html
