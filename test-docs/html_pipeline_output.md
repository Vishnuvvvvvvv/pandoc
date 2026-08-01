This document is generated specifically to test the Pandoc extraction pipeline. It contains complex multi-level tables with merged cells and coloured headers, nested bullet and numbered lists, inline text formatting, and multi-paragraph cells — all structures that cause Pandoc to emit HTML table tags.

# 1. Inline Text Formatting

**Bold red text.** *Italic text.* Underlined text. ***Bold + Italic + Blue.***

# 2. Nested Bullet & Numbered Lists

- Alpha Product Line

- Sub-category A

- Detail level — specifications here

- Sub-category B

- Detail level — specifications here

- Beta Product Line

- Sub-category A

- Detail level — specifications here

- Sub-category B

- Detail level — specifications here

- Gamma Product Line

- Sub-category A

- Detail level — specifications here

- Sub-category B

- Detail level — specifications here

## Numbered Steps

1. Gather all input DOCX files.
2. Run them through the Pandoc pipeline.
3. Review the generated Markdown output.
4. Verify with the QA team.

# 3. Simple Product Table

This is a standard table Pandoc should render as a GFM pipe table:

| **Product ID** | **Name** | **Category** | **Price (£)** |
| --- | --- | --- | --- |
| P-001 | Funeral Plan Gold | Insurance | £4,500 |
| P-002 | Funeral Plan Silver | Insurance | £3,200 |
| P-003 | Probate Service | Legal | £1,800 |
| P-004 | Will Writing Kit | Legal | £299 |

# 4. Complex Merged-Cell Table (triggers HTML output)

This table uses cell merging (colspan/rowspan). Pandoc cannot represent this in standard Markdown, so it will emit HTML <table> tags.

| **ANNUAL PRODUCT LIFECYCLE SUMMARY** | | | | |
| --- | --- | --- | --- | --- |
| **Product Details** | | **Financial Performance** | | **Status** |
| **Code** | **Name** | **Q1 Revenue** | **Q2 Revenue** | **Lifecycle** |
| P-001 | Funeral Plan Gold | £420,000 | £380,000 | **Active** |
| P-002 | Funeral Plan Silver | £290,000 | £310,000 | **Active** |
| P-003 | Probate Service | £120,000 | £145,000 | **Under Review** |

# 5. Table With Multi-Paragraph Cells

Cells with multiple paragraphs also force Pandoc into HTML mode because GFM pipe tables cannot contain newlines inside a cell.

| **Feature** | **Description** | **Notes** |
| --- | --- | --- |
| Extraction Engine | Primary: Pandoc (gfm)  Fallback: MarkItDown  Legacy: Docling | Configured per router |
| Output Format | GitHub Flavoured Markdown (.md) | Stored in SharePoint |

# 6. Important Notes

> All documents processed by this pipeline are subject to the 3-stage lifecycle: Raw → Draft → Final. No document reaches downstream AI systems until it has been signed off by an authorised verifier.

# 7. Wide Multi-Column Data Table (forces HTML)

A table with 8+ narrow columns where the header row spans 2 levels also cannot be expressed in GFM — Pandoc will emit HTML.

| **Q1 Metrics** | | | | **Q2 Metrics** | | | |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Jan** | **Feb** | **Mar** | **Total Q1** | **Apr** | **May** | **Jun** | **Total Q2** |
| 120 | 135 | 142 | 397 | 150 | 160 | 175 | 485 |
| 200 | 210 | 195 | 605 | 220 | 230 | 215 | 665 |
| 80 | 90 | 85 | 255 | 100 | 110 | 95 | 305 |