---
title: Scope of Review Test
---

# Document Review Report

This document contains a complex outer table with a bold title row and a nested table inside one of its cells.

<table>
<tbody>
<tr>
<td><strong>SCOPE OF REVIEW</strong></td>
</tr>
<tr>
<td>
<p>The following areas were reviewed as part of the annual audit:</p>
<!-- Nested table inside the outer table cell -->
<table>
<thead>
<tr>
<th>Area</th>
<th>Status</th>
<th>Risk Level</th>
</tr>
</thead>
<tbody>
<tr>
<td>Financial Controls</td>
<td><strong>Compliant</strong></td>
<td>Low</td>
</tr>
<tr>
<td>Data Security</td>
<td><em>Partially Compliant</em></td>
<td>Medium</td>
</tr>
<tr>
<td>Regulatory Adherence</td>
<td><strong>Non-Compliant</strong></td>
<td>High</td>
</tr>
</tbody>
</table>
<p>All findings are documented in Appendix B.</p>
</td>
</tr>
</tbody>
</table>

## Additional Simple Table

This table has no nesting — should become a clean pipe table.

| Quarter | Revenue | Profit |
| ------- | ------- | ------ |
| Q1      | £1.2M   | £340K  |
| Q2      | £1.5M   | £420K  |
| Q3      | £1.1M   | £290K  |

## Rowspan Table

This table has rowspans — should be expanded by our preprocessor.

| Department  | Team Lead      | Member |
| ----------- | -------------- | ------ |
| Engineering | **Sarah Chen** | Alice  |
| Engineering | **Sarah Chen** | Bob    |
| Engineering | **Sarah Chen** | Carol  |
| Marketing   | *Dave Wilson*  | Eve    |
| Marketing   | *Dave Wilson*  | Frank  |
