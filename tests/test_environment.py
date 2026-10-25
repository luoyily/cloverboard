from cloverboard.logger.environment import collect_environment


def test_collect_environment_without_hardware():
    environment = collect_environment(capture_hardware=False)

    assert isinstance(environment["argv"], list)
    assert environment["python_version"]
    assert environment["platform"]
    assert "gpus" not in environment


def test_collect_environment_with_hardware_does_not_raise():
    environment = collect_environment(capture_hardware=True)

    assert "python_version" in environment
