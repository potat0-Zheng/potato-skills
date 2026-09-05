class Weibo:
    __slots__ = (
        'id', 'user_id', 'content', 'article_url', 'original_pictures',
        'retweet_pictures', 'original', 'video_url', 'original_pictures_list',
        'retweet_pictures_list', 'media', 'publish_place', 'publish_time',
        'publish_tool', 'up_num', 'retweet_num', 'comment_num'
    )

    def to_dict(self):
        """将对象转换为字典"""
        return {slot: getattr(self, slot) for slot in self.__slots__ if hasattr(self, slot)}

    def __init__(self):
        self.id = ''
        self.user_id = ''
        self.content = ''
        self.article_url = ''
        self.original_pictures = []
        self.retweet_pictures = []
        self.original = True
        self.video_url = ''
        self.original_pictures_list = []
        self.retweet_pictures_list = []
        self.media = {}
        self.publish_place = ''
        self.publish_time = ''
        self.publish_tool = ''
        self.up_num = 0
        self.retweet_num = 0
        self.comment_num = 0

    def __str__(self):
        """打印一条微博"""
        result = self.content + '\n'
        result += u'微博发布位置：%s\n' % self.publish_place
        result += u'发布时间：%s\n' % self.publish_time
        result += u'发布工具：%s\n' % self.publish_tool
        result += u'点赞数：%d\n' % self.up_num
        result += u'转发数：%d\n' % self.retweet_num
        result += u'评论数：%d\n' % self.comment_num
        result += u'url：https://weibo.cn/comment/%s\n' % self.id
        return result
