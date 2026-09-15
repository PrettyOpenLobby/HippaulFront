"""Readers that regenerate the server's fmodata tables from a Front Mission
Online PC client install (the directory containing Data/, PolBoot.exe and
FrontMissionOnline.dll).

Each module is runnable standalone (python tools/fmodatagen/<name>.py) and
takes the install directory with --client; none of them carries a default
install path. tools/fmodata_build.py runs them all in order.

Modules:
  fmofile        resource index -> Data/<AA>/F<nn>/D<nn>.DAT path
  fmofmdt        the FMDT obfuscation codec and the MSG record parser
  fmofmdtwrite   MSG container split/rebuild (record and run enumeration)
  fmoitm         the ITM item-table container (cosmetics, insignia)
  fmoclass       AI/F32/D15.DAT -> fmo-class-exp.tsv, fmo-ranks.tsv
  fmocosmetics   BB/F13/D27..D31 -> fmo-cosmetics.tsv
  fmoinsignia    BB/F13/D31 -> fmo-insignia.tsv
  fmomissiongen  the mission-catalogue text rules (titles, zones, regexes)
  fmoprogression the mission/cutscene join -> fmo-missions.tsv, fmo-cutscenes.tsv
  fmomap         map-container header and world-space bounding boxes
  fmoscriptcast  syscall parameter recovery from compiled SCP scripts
  fmotim2        TIM2 texture decoder (the City Control board backdrop)
"""
