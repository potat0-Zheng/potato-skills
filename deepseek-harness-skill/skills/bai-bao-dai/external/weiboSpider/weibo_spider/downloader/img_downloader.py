import os

from .downloader import Downloader


class ImgDownloader(Downloader):
    def __init__(self, file_dir, file_download_timeout):
        super().__init__(file_dir, file_download_timeout)
        self.describe = u'图片'
        self.key = ''

    async def handle_download(self, urls, w, session):
        """处理下载相关操作"""
        file_prefix = w.publish_time[:10].replace('-', '') + '_' + w.id
        file_dir = self.file_dir + os.sep + self.describe
        if not os.path.isdir(file_dir):
            os.makedirs(file_dir)
        media_key = self.key or 'original_pictures'
        if ',' in urls:
            url_list = urls.split(',')
            for i, url in enumerate(url_list):
                index = url.rfind('.')
                if len(url) - index >= 5:
                    file_suffix = '.jpg'
                else:
                    file_suffix = url[index:]
                file_name = file_prefix + '_' + str(i + 1) + file_suffix
                file_path = file_dir + os.sep + file_name
                ok = await self.download_one_file(url, file_path, w.id, session)
                if ok:
                    w.media.setdefault(media_key, []).append({
                        'url': url,
                        'path': file_path
                    })
        else:
            index = urls.rfind('.')
            if len(urls) - index > 5:
                file_suffix = '.jpg'
            else:
                file_suffix = urls[index:]
            file_name = file_prefix + file_suffix
            file_path = file_dir + os.sep + file_name
            ok = await self.download_one_file(urls, file_path, w.id, session)
            if ok:
                w.media.setdefault(media_key, []).append({
                    'url': urls,
                    'path': file_path
                })
