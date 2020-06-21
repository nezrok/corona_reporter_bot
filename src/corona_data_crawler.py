import logging
import urllib.request
import xlrd

# =================================================================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s : %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__file__)

# =================================================================================================


def crawl(excel_file_url):
    """
    Creates an html report containing the latest case numbers.
    """
    # Download the excel file containing the corona statistics from BaWü (broken down by counties).
    excel_file_path = "corona-report.xlsx"
    log.info(f"Downloading excel file from '{excel_file_url}' ...")
    urllib.request.urlretrieve(excel_file_url, excel_file_path)

    # Parse the excel file.
    log.info("Parsing excel file ...")
    workbook = xlrd.open_workbook(excel_file_path)
    # Read the first sheet containing the infected cases ("Infizierte Coronavirus in BW")
    infections = crawl_excel_sheet(workbook.sheet_by_index(0))
    # Read the second sheet containing the death cases ("Todesfälle Coronavirus in BW")
    deaths = crawl_excel_sheet(workbook.sheet_by_index(1))

    return infections, deaths


def crawl_excel_sheet(sheet):
    """
    Reads the corona statistics from the given excel sheet.
    """
    ncols = sheet.ncols

    # Read the dates from row #7.
    # dates = [sheet.cell_value(6, i) for i in range(ncols)]

    # Read the statistics per county.
    stats_per_county = {}
    for i in range(7, 52):  # County-specific statistics are in rows 8-52.
        county = sheet.cell_value(i, 0)
        stats = []
        for j in range(1, ncols):
            val = sheet.cell_value(i, j)
            stats.append(val if val != "" else 0.0)
        stats_per_county[county] = stats

    # Add synonym "Baden-Württemberg" as synonym for "Summe".
    stats_per_county["Baden-Württemberg"] = stats_per_county["Summe"]

    return stats_per_county
