"""只用合成身份和敏感值，验证隐私拦截不触发论坛或邮件副作用。"""
import json

import pytest

from wpy import ai_agent as ai
from wpy.ai_agent_privacy import PrivacyGuard
from .test_ai_agent import Remote, proposal, read


@pytest.mark.parametrize("sensitive", [
    "联系 reader@example.invalid", "电话 " + "138" + "0000" + "0000",
    "学号 " + "30" + "12345678", "证件 " + "110101" + "19900101" + "123X",
    "秘密 test_private_password", "秘密 TEST_PRIVATE_PASSWORD", "位置 /opt/private/config.json",
    "电话 " + "１ ３ ８ ０ ０ ０ ０ ０ ０ ０ ０",
])
def test_sensitive_text_cannot_reach_forum_or_report_outbox(tmp_path, sensitive):
    remote = Remote()
    with ai.worker_lock(tmp_path):
        store = ai.Store(tmp_path, "test-personal")
        try:
            guard = PrivacyGuard(["test_private_password"])
            executor = ai.Executor(ai.Forum("test_personal_token", request=remote), store,
                                   execute=True, report=True, privacy=guard)
            read(executor)
            with pytest.raises(ai.Denied, match="个人信息"):
                executor.run(proposal("comment_post", post_id=101, content=sensitive))
            with pytest.raises(ai.Denied, match="个人信息"):
                executor.run(proposal("create_post", board=2, title=sensitive, content="合成日常闲聊"))
            assert not remote.writes
            assert store.db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0
            assert store.db.execute("SELECT count(*) FROM agent_mail").fetchone()[0] == 0
        finally:
            store.close()


def test_model_input_is_redacted_without_putting_private_values_in_requests():
    guard = PrivacyGuard(["test_private_password", "test_owner_name"])
    seen = []
    def request(url, **kwargs):
        payload = json.loads(kwargs["body"])
        seen.append(payload)
        body = kwargs["body"]
        assert "test_private_password" not in body and "test_owner_name" not in body
        assert "reader@example.invalid" not in body
        return {"choices": [{"message": {"content": '{"action":"stop","arguments":{}}'}}]}
    model = ai.Model(ai.ModelConfig("https://example.invalid", "test_model_key", "test-model"), request=request)
    model.privacy = guard
    model.next([{"role": "system", "content": "正常规则"},
                {"role": "user", "content": "test_private_password test_owner_name reader@example.invalid"}])
    assert seen[0]["messages"][0]["content"] == "正常规则"
    assert seen[0]["messages"][1]["content"] == "[已隐去敏感内容]"


def test_plain_chat_is_preserved_and_private_values_are_not_in_repr():
    guard = PrivacyGuard(["test_private_password"])
    assert not guard.contains("本鲸来湖底摸个鱼，周末快乐喵。")
    assert guard.redact({"post_id": 101, "title": "合成闲聊"}) == {"post_id": 101, "title": "合成闲聊"}
    assert "test_private_password" not in repr(guard)
