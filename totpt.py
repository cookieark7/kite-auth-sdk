import hmac,hashlib,struct

def truncate(mac:bytes,digits:int =6)->str:
    # mac is a 20 bytes HMAC-SHA1 OUTput which is 80 bits
    
    offset = mac[-1] & 0x0f
    
    chunk = (
        (mac[offset] <<24)
        | (mac[offset + 1] <<16)
        | (mac[offset + 2] <<8)
        | mac[offset + 3]
    )
    
    # step 3 clear the top (sign) bit -> a portable non- -ve 31 bit int
    code = chunk & 0x7FFFFFFF
    
    return str(code % (10**digits)).zfill(digits)

def hotp(key:bytes,counter:int)->str:
    msg = struct.pack(">Q",counter)
    print(msg,"in msg")
    mac = hmac.new(key, msg, hashlib.sha1).digest()
    print()
    return truncate(mac)


key = b"hello"

counter = 1

print(hotp(key,counter))
