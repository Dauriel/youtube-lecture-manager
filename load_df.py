import requests
import numpy as np
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
URL = "CSV_URL"


def download_last_version(
    url: str = URL,
    df_path: str = "lectures.csv",
):
    with open(df_path, "wb") as f:
        resp = requests.get(
            url,
            verify=False,
        )
        f.write(resp.content)
