#!/bin/bash
<<COMMENT
#
# 2024-06-10 Orin JetPack6.0GA固件升级脚本V1.0_方竹科技
#
COMMENT

clear
str_ver="# R36 (release), REVISION: 4.3"

red_print(){
	echo -e "\e[1;31m$1\e[0m"
}
green_print(){
	echo -e "\e[1;32m$1\e[0m"
}

red_print "------------FG24-4CH Orin NX/Nano Devkit Uninstall FZCAM ----------------------"
echo ''
echo $PWD

str=$(cat /etc/nv_tegra_release)
if [[ $str == *$str_ver* ]]; then
	echo $str_ver
	green_print 'JetPack is corret, JetPack版本匹配继续'
else
	echo $str_ver
	red_print 'JetPack is not corret check and run again, JetPack版本不匹配，检查后再运行'
	exit
fi

green_print 'Press Enter to continue'

echo '' 
if [ -e /lib/modules/$(uname -r)/updates/drivers/media/i2c/fzcam.ko ]
then
	echo 'Remove fzcam module'
	sudo rm /lib/modules/$(uname -r)/updates/drivers/media/i2c/fzcam.ko
fi

if [ -e /etc/fzcam_cfg.ini ]
then
	echo 'Remove fzcam cfg ini'
	sudo rm /etc/fzcam_cfg.ini
fi

if [ -e /usr/local/bin/fzcam_ui ]
then
	echo 'Remove fzcam_ui'
	sudo rm /usr/local/bin/fzcam_ui
fi

if [ -e /usr/local/bin/fzcam_cfg ]
then
	echo 'Remove fzcam_cfg'
	sudo rm /usr/local/bin/fzcam_cfg
fi

sudo systemctl disable fzcam_cfg.service 
sudo rm /etc/systemd/system/fzcam_cfg.service       

if [ -e /boot/tegra234-p3767-camera-p3768-fzcam-fg24-4ch-4lanes.dtbo ]
then
	echo 'Remove fg24-4ch dtbo'
	sudo rm /boot/tegra234-p3767-camera-p3768-fzcam-fg24-4ch-4lanes.dtbo
fi

if [ -e /boot/tegra234-p3767-camera-p3768-fzcam-fg24-4ch-2lanes.dtbo ]
then
	echo 'Remove fg24-4ch dtbo'
	sudo rm /boot/tegra234-p3767-camera-p3768-fzcam-fg24-4ch-2lanes.dtbo
fi


green_print 'Remove fzcam success, please press Enter to reboot Jetson Orin'
read key

sudo reboot

