import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import os
import shutil

from weibo_spider.downloader.downloader import Downloader
from weibo_spider.downloader.img_downloader import ImgDownloader

class MockWeibo:
    def __init__(self):
        self.id = '12345'
        self.publish_time = '2023-10-27 10:00:00'
        self.media = {}
        self.original_pictures = 'http://example.com/pic.jpg'

class TestDownloaderAsync(unittest.TestCase):
    def setUp(self):
        self.test_dir = 'tests/tmp_downloader'
        if not os.path.exists(self.test_dir):
            os.makedirs(self.test_dir)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_img_downloader(self):
        async def run_test():
            downloader = ImgDownloader(self.test_dir, [1, 1, 1])
            downloader.key = 'original_pictures' # Set key explicitly
            weibo = MockWeibo()
            
            mock_session = MagicMock()
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.read.return_value = b'fake_image_content'
            
            # Mock session.get to return an async context manager
            mock_context = AsyncMock()
            mock_context.__aenter__.return_value = mock_response
            mock_context.__aexit__.return_value = None
            mock_session.get.return_value = mock_context

            # Patch asyncio.sleep to speed up tests
            with patch('asyncio.sleep', AsyncMock()):
                # Test download_files
                await downloader.download_files([weibo], mock_session)
            
            # Check if file exists
            expected_file = os.path.join(self.test_dir, '图片', '20231027_12345.jpg')
            self.assertTrue(os.path.exists(expected_file), f"File {expected_file} does not exist")
            
            # Check content
            with open(expected_file, 'rb') as f:
                content = f.read()
                self.assertEqual(content, b'fake_image_content')

        asyncio.run(run_test())

if __name__ == '__main__':
    unittest.main()
