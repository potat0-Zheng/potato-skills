import os

from .downloader import Downloader


class VideoDownloader(Downloader):
    def __init__(self, file_dir, file_download_timeout):
        super().__init__(file_dir, file_download_timeout)
        self.describe = u'视频'
        self.key = 'video_url'

    async def handle_download(self, urls, w, session):
        """处理下载相关操作"""
        file_prefix = w.publish_time[:10].replace('-', '') + '_' + w.id
        file_suffix = '.mp4'
        file_name = file_prefix + file_suffix
        file_path = self.file_dir + os.sep + file_name
        ok = await self.download_one_file(urls, file_path, w.id, session)
        if ok:
            w.media.setdefault('video', []).append({
                'url': urls,
                'path': file_path
            })
