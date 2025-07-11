# OrCADtoKiCAD
# This is still a WIP, where it is not functioning refrenced pictures below
# Requires Kicad Version:9+ 
# python 3.6+ 

# To run:
1. cd into <OrCADtoKiCAD/orcad kicad converter example/orcad>
2. run: "python3 orcad2kicad_sch.py --xml example.xml --out design.kicad_sch"
3. Open design.kicad_sch in Kicad 9
4. you will see some of the things are converted


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


