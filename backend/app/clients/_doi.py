"""DOI 정규화. KCI/OpenAlex가 서로 다른 URL 형태로 DOI를 주므로 bare `10.x/...`로 통일한다."""

_PREFIXES = (
    "http://dx.doi.org/", "https://dx.doi.org/",
    "http://doi.org/", "https://doi.org/",
    "doi.org/", "dx.doi.org/",
)


def strip_doi_prefix(doi: str | None) -> str | None:
    if not doi:
        return None
    s = doi.strip()
    if not s:
        return None
    for prefix in _PREFIXES:
        if s.startswith(prefix):
            return s[len(prefix):].lower()
    return s.lower()
