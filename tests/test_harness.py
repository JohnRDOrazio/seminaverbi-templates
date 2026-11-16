from lua_env import LuaEnv


def test_args_merge():
    env = LuaEnv()
    frame = env.frame({"ref": " Jn 3:16 "}, {"ref": "x", "version": ""})
    args = env.call("Module:SV/Args", "get", frame)
    assert dict(args.items()) == {"ref": "Jn 3:16"}


def test_escape():
    env = LuaEnv()
    assert env.call("Module:SV/Util", "escape", 'a"<b>&') == "a&quot;&lt;b&gt;&amp;"


def test_msg_fallback():
    env = LuaEnv(data={"i18n": {"en": {"k": "E"}, "fr": {}}})
    assert env.call("Module:SV/Util", "msg", "k", "fr") == "E"
    assert env.call("Module:SV/Util", "msg", "zz", "fr") == "zz"
