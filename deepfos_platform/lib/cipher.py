import codecs
from hashlib import pbkdf2_hmac

from Crypto.Cipher import AES
from Crypto.Cipher.AES import MODE_CBC

from .constants import AES_ENCRYPT_KEY, AES_SALT


def maybe_unpad(decrypted: bytes) -> str:
    if decrypted[-1] in range(1, 17):
        pad_length = decrypted[-1]
        pad_char = chr(decrypted[-1])
        if decrypted.decode("utf-8").endswith(pad_char * pad_length):
            return decrypted.decode("utf-8")[:-pad_length]
    return decrypted.decode("utf-8")


def maybe_decrypt_pwd(password: str):
    # remove {cipher} at the beginning
    if not password.startswith('{cipher}'):
        return password

    encrypted_pwd = password[8:]
    key = pbkdf2_hmac(
        hash_name='sha1',
        password=AES_ENCRYPT_KEY,
        salt=codecs.decode(AES_SALT, 'hex'),
        iterations=1024,
        dklen=32
    )
    aes = AES.new(key, mode=MODE_CBC)
    decrypted = aes.decrypt(codecs.decode(encrypted_pwd.encode('utf-8'), 'hex'))[16::]
    return maybe_unpad(decrypted)