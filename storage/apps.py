from django.apps import AppConfig


class StorageConfig(AppConfig):
    default = True
    verbose_name = "数据层"

    # 代码在 storage/，去这里读 models / admin / migrations。
    name = "storage"

    # label 必须保持 "web"，不要跟着目录一起改。
    #
    # 有 7 张表的表名是 Django 从 app label 推导的（这些 model 没写 db_table）：
    #   web_character / web_friend / web_message / web_messageattachment
    #   web_systemprompt / web_userprofile / web_voice
    # django_migrations 里还有 30 条 app='web' 的历史记录。
    #
    # label 决定"认哪条历史"，name 决定"去哪读代码"，Django 允许这两者不同。
    # 改 label 需要一次表重命名迁移，代价高且没必要。
    #
    # 副作用：showmigrations 会一直显示 label 为 web。
    label = "web"
