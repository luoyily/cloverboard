from cloverboard import init, log, log_config
from cloverboard.logger.context import get_context


def test_facade_exports_public_api():
    assert callable(init)
    assert callable(log)
    assert callable(log_config)


def test_init_sets_global_context(tmp_path):
    init(exp_name="demo", logdir=tmp_path, config={"lr": 1e-3})

    context = get_context()
    assert context is not None
    assert context.exp_name == "demo"
    assert context.logdir == tmp_path
    assert context.config == {"lr": 1e-3}


def test_calls_without_init_are_noops():
    log({"loss": 0.5}, step=1)
    log_config({"lr": 1e-3})


def test_log_and_log_config_do_not_raise_after_init(tmp_path):
    init(logdir=tmp_path)

    log_config({"lr": 1e-3})
    log({"train/loss": 0.5}, step=1)
