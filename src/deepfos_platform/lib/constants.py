import os

PROJECT_BASE = os.path.dirname(os.path.dirname(__file__))
APP_NAME = os.path.basename(PROJECT_BASE)

AES_ENCRYPT_KEY = b"APODNU3093r2rbjzxcn09u213asdhy08WRFH"
AES_SALT = b"deadbeef"

ENV_DEVEL_ENV = "DEEPFOS_COMPONENT_ENV"
ENV_TEST_MODE = "DEEPFOS_COMPONENT_TEST_MODE"

INTERNAL_TOKEN_HEADER = "X-deepfos-internal-key"

BOOLEAN_STATES = {
    "1": True, "yes": True, "true": True, "on": True,
    "0": False, "no": False, "false": False, "off": False,
}
