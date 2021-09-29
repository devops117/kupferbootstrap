from shutil import which


def programs_available(programs) -> bool:
    if type(programs) is str:
        programs = [programs]
    for program in programs:
        if not which(program):
            return False
    return True
