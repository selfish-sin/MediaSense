import sqlite3
DB = r"D:\projects\youtube_tool_v2\backend\data\youtube_tool.db"
c = sqlite3.connect(DB)
c.execute("DELETE FROM job_chats WHERE job_id='test-chat-job'")
c.execute("DELETE FROM jobs WHERE id='test-chat-job'")
c.execute("DELETE FROM model_configs WHERE key='mock-stream'")
c.commit()
print("已清理测试数据行: jobs/job_chats/mock-stream")
