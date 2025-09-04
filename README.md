# OrCADtoKiCAD
# This is still a WIP, where it is not functioning refrenced pictures below
# Requires Kicad Version:9+ 
# python 3.6+ 

# step 1: Importing and converting OrCAD symbol libraries: 
first, extract .OLB symbol files from OrCAD(the symbol lib's used by schematic)
then we need to use the OrCAD symbol-> log converter borrowed from https://github.com/Werni2A/OpenOrCadParser
this should by default output into a tmp folder the new logs


# step 2: from logs to kicad symbol lib:
cd into <OrCADtoKiCAD/orcad kicad converter example/orcad>
first we need to convert the logs from step 1: to kicad.sch
this is done by run_all_logs.py "path to log created" --scale 0.254(standard scale conversion OrCAD to KiCAD) --convert-script(based on type of log we want to convert) --out-dir(where to write the results of symbol conversion, can be updated with more symbols, supports multiple --convert-script)
cmd: python3 run_all_logs.py   "/tmp/OpenOrCadParser/0ba6d31e51a6d51ac704fa84247fa4d1/logs/nat_semi.olb/Packages/"   --scale 0.254   --convert-script "/home/hnp/Desktop/OrCADtoKiCAD/orcad kicad converter example/orcad/convert_log.py"   --out-dir "/home/hnp/Desktop/OrCADtoKiCAD/orcad kicad converter example/orcad/converted"
however depending on type of OrCAD symbol library and format they use, we need to change "--convert-script" used. current options are convert_log.py & convert_dsn.py" 
when this is run we get a kicad symbol library, this can be imported into kicad.

# step 3: converting the schematic: 
export orcad schematic as xml
then run:  python3 orcad2kicad_sch.py   --xml(path to the xml for the design schematic)   --out "design.kicad_sch"   --symdir /usr/share/kicad/symbols   --converted-dir(step 2, converted symbol libraries)   --converted-lib-file(step 2, converted symbol library dict)   --converted-lib(new symbol sch.kicad_sym) --converted-lib converted(library name in kicad) 
cmd: python3 orcad2kicad_sch.py   --xml "/home/hnp/Desktop/new git/OrCADtoKiCAD/orcad kicad converter example/orcad/example.xml"   --out "design.kicad_sch"   --symdir /usr/share/kicad/symbols   --converted-dir "/home/hnp/Desktop/new git/OrCADtoKiCAD/orcad kicad converter example/orcad/converted"   --converted-lib-file "/home/hnp/Desktop/new git/OrCADtoKiCAD/orcad kicad converter example/orcad/converted/converted_sch.kicad_sym"   --converted-lib converted
then open in kicad and it should be converted


# What we want to convert: 
<img width="3440" height="1440" alt="image" src="https://github.com/user-attachments/assets/45660ca7-1f85-451a-b57a-8a40cff4a789" />


# What we get when running conversion script: 
<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/23f2c5f0-7e19-4829-b82b-24088bdc273a" />

# What we should get: 
<img width="3440" height="1440" alt="image" src="https://github.com/user-attachments/assets/e4868a41-75db-44d0-becd-580c8318e177" />

# Consist of another script that loads the symbols more manually, gets all symbols except 1. however it is messy and also a WIP: 
# how to run: 
1. python3 ortokicad.py --xml example.xml --out design.kicad_sch

# What this script gives: 
<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/1e134438-ba0b-402a-9386-fb0abf090c6e" />


# This project is still a work in progress and uses some manuall definitions for the symbols, however it is a start!


