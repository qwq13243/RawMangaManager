from scrapers import KlMangaScraper, NicoMangaScraper, RawkumaScraper

# 注册爬虫实例
SCRAPERS = {
    "klmanga": KlMangaScraper(),
    "nicomanga": NicoMangaScraper(),
    "rawkuma": RawkumaScraper()
}
