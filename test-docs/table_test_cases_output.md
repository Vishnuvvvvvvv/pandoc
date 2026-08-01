---
title: Table Test Cases
---

# Table Conversion Test Cases

## 1. Simple Table (no merging)

A basic 3-column table. Should convert to a clean GFM pipe table.

| Product  | Category    | Price |
| -------- | ----------- | ----- |
| Laptop   | Electronics | £999  |
| Desk     | Furniture   | £249  |
| Notebook | Stationery  | £3    |

## 2. Table with Colspan (merged columns)

A two-level header where the top row spans multiple columns.

| Q1 Sales Report | | |
| --------------- | ---------- | ------- |
| Region          | Units Sold | Revenue |
| North           | 1,200      | £48,000 |
| South           | 950        | £38,000 |
| East            | 1,500      | £60,000 |
| West            | 800        | £32,000 |

## 3. Table with Rowspan (merged rows)

A table where the first column cell spans multiple rows.

| Department  | Employee | Role           |
| ----------- | -------- | -------------- |
| Engineering | Alice    | Backend Dev    |
| Engineering | Bob      | Frontend Dev   |
| Engineering | Carol    | DevOps         |
| Marketing   | Dave     | SEO Specialist |
| Marketing   | Eve      | Content Writer |

## 4. Complex Table (colspan + rowspan combined)

A product lifecycle summary with both merged rows and columns.

| ANNUAL PRODUCT LIFECYCLE SUMMARY | | | | |
| -------------------------------- | ------------------- | --------------------- | ---------- | -------------- |
| Product Details                  | | Financial Performance | | Status         |
| Code                             | Name                | Q1 Revenue            | Q2 Revenue | Lifecycle      |
| P-001                            | Funeral Plan Gold   | £420,000              | £380,000   | **Active**     |
| P-002                            | Funeral Plan Silver | £290,000              | £310,000   | **Active**     |
| P-003                            | Probate Service     | £120,000              | £145,000   | *Under Review* |

## 5. Table with Multi-Paragraph Cells

Cells containing multiple paragraphs of text.

| Feature           | Description                                                          | Notes                 |
| ----------------- | -------------------------------------------------------------------- | --------------------- |
| Extraction Engine | Primary: Pandoc (HTML mode)   Fallback: MarkItDown   Legacy: Docling | Configured per router |
| Output Format     | GitHub Flavoured Markdown (.md)                                      | Stored in SharePoint  |

## 6. Nested Table (table inside a table cell)

The outer table has a cell that contains a complete inner table.

<table>
<thead>
<tr>
<th>Inner Table (nested)</th>
<th>Description</th>
</tr>
</thead>
<tbody>
<tr>
<td>
<!-- This is the inner/nested table -->
<table>
<thead>
<tr><th>A</th><th>B</th></tr>
</thead>
<tbody>
<tr><td>1</td><td>2</td></tr>
<tr><td>3</td><td>4</td></tr>
</tbody>
</table>
</td>
<td>To the left is a nested table inside an outer table cell.</td>
</tr>
</tbody>
</table>

## 7. Wide Table (many columns, forced horizontal scroll)

An 8-column monthly metrics table with a merged header.

| Q1 Metrics | | | | Q2 Metrics | | | |
| ---------- | --- | --- | -------- | ---------- | --- | --- | -------- |
| Jan        | Feb | Mar | Q1 Total | Apr        | May | Jun | Q2 Total |
| 120        | 135 | 142 | 397      | 150        | 160 | 175 | 485      |
| 200        | 210 | 195 | 605      | 220        | 230 | 215 | 665      |
