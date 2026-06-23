from __future__ import annotations

import io

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font

from .utils import usaspending_recipient_profile_url


def build_awards_export_frame(awards: pd.DataFrame) -> pd.DataFrame:
    if awards is None or awards.empty:
        return pd.DataFrame(
            columns=[
                "Contractor",
                "Recipient UEI",
                "Recipient Profile URL",
                "Award ID",
                "Award URL",
                "Description",
                "Obligations in Scope",
                "Current Award Value",
                "Award Ceiling",
                "Performance Location",
                "Awarding Office",
                "Funding Office",
            ]
        )
    rows = []
    for row in awards.to_dict("records"):
        contractor = str(row.get("Contractor") or "")
        recipient_uei = str(row.get("Recipient UEI") or "")
        rows.append(
            {
                "Contractor": contractor,
                "Recipient UEI": recipient_uei,
                "Recipient Profile URL": usaspending_recipient_profile_url(recipient_uei, contractor),
                "Award ID": row.get("Award ID"),
                "Award URL": row.get("USAspending Award Link"),
                "Description": row.get("Description"),
                "Obligations in Scope": row.get("Obligations in Scope"),
                "Current Award Value": row.get("Current Award Value"),
                "Award Ceiling": row.get("Award Ceiling"),
                "Performance Location": row.get("Performance Location"),
                "Awarding Office": row.get("Awarding Office"),
                "Funding Office": row.get("Funding Office"),
            }
        )
    return pd.DataFrame(rows)


def awards_export_csv(export_df: pd.DataFrame) -> bytes:
    return export_df.to_csv(index=False).encode("utf-8-sig")


def awards_export_xlsx(export_df: pd.DataFrame) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Top Relevant Awards"
    headers = list(export_df.columns)
    worksheet.append(headers)
    link_font = Font(color="0563C1", underline="single")
    contractor_col = headers.index("Contractor") + 1
    profile_col = headers.index("Recipient Profile URL") + 1
    award_id_col = headers.index("Award ID") + 1
    award_url_col = headers.index("Award URL") + 1
    for row in export_df.to_dict("records"):
        worksheet.append([row.get(header) for header in headers])
        row_idx = worksheet.max_row
        profile_url = str(row.get("Recipient Profile URL") or "")
        award_url = str(row.get("Award URL") or "")
        contractor_cell = worksheet.cell(row=row_idx, column=contractor_col)
        if profile_url:
            contractor_cell.hyperlink = profile_url
            contractor_cell.font = link_font
        award_cell = worksheet.cell(row=row_idx, column=award_id_col)
        if award_url:
            award_cell.hyperlink = award_url
            award_cell.font = link_font
        profile_cell = worksheet.cell(row=row_idx, column=profile_col)
        if profile_url:
            profile_cell.hyperlink = profile_url
            profile_cell.font = link_font
        award_url_cell = worksheet.cell(row=row_idx, column=award_url_col)
        if award_url:
            award_url_cell.hyperlink = award_url
            award_url_cell.font = link_font
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()
