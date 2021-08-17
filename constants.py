FASTBOOT = 'fastboot'

ROOTFS = 'rootfs'
BOOTIMG = 'bootimg'
LK2ND = 'lk2nd'
QHYPSTUB = 'qhypstub'

EMMC = 'emmc'
EMMCFILE = 'emmc-file'
MICROSD = 'microsd'
LOCATIONS = [EMMC, EMMCFILE, MICROSD]

JUMPDRIVE = 'jumpdrive'
JUMPDRIVE_VERSION = '0.8'

BOOT_STRATEGIES = {
    'oneplus-enchilada': FASTBOOT,
    'xiaomi-beryllium-ebbg': FASTBOOT,
    'xiaomi-beryllium-tianma': FASTBOOT,
    'bq-paella': FASTBOOT,
}

DEVICES = {
    'oneplus-enchilada': ['sdm845-oneplus-enchilada'],
    'xiaomi-beryllium-ebbg': ['sdm845-xiaomi-beryllium-ebbg'],
    'xiaomi-beryllium-tianma': ['sdm845-xiaomi-beryllium-tianma'],
    'bq-paella': ['msm8916-bq-paella'],
}

FLAVOURS = {
    'barebone': [],
    'phosh': [],
    'plasma-mobile': [],
}
