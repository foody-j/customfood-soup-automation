#!/bin/bash
<<COMMENT
#
# 2024-06-10 AGX Orin JetPack6.0GA固件升级脚本V1.0_方竹科技
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

red_print "------------FG24-4CH Orin NX/Nano Devkit 固件升级Firmware Upgrade----------------------"
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
read key

echo '' 
sudo cp rootfs/lib/modules/$(uname -r)/updates/drivers/media/i2c/fzcam.ko /lib/modules/$(uname -r)/updates/drivers/media/i2c/
sudo insmod /lib/modules/$(uname -r)/updates/drivers/media/i2c/fzcam.ko
sudo depmod

sudo cp fzcam_app/etc/fzcam_cfg.ini /etc/
sudo cp fzcam_app/usr/local/bin/fzcam_cfg /usr/local/bin/
sudo cp fzcam_app/usr/local/bin/fzcam_ui /usr/local/bin/
sudo chmod +x /usr/local/bin/fzcam_ui
sudo chmod +x /usr/local/bin/fzcam_cfg

sudo cp fzcam_app/fzcam_cfg.service /etc/systemd/system/
sudo chmod 644 /etc/systemd/system/fzcam_cfg.service 
sudo systemctl enable  /etc/systemd/system/fzcam_cfg.service 
sudo systemctl daemon-reload

sudo cp rootfs/boot/tegra234-p3767-camera-p3768-fzcam-fg12-4ch-4lanes.dtbo /boot/
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n 2="Camera FG12-4CH-4Lanes-YUV"

green_print 'Upgrade FW success, please press Enter to reboot Jetson Orin'
read key

sudo reboot

