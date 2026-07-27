sudo -S su <<EOF
nvidia

sudo busybox devmem 0x02434020 w 0x6
sudo busybox devmem 0x02440020 w 0x6

sudo sh -c 'echo timer > /sys/class/leds/camsync0-gpio/trigger'
sudo sh -c 'echo 10 > /sys/class/leds/camsync0-gpio/delay_on; echo 20 > /sys/class/leds/camsync0-gpio/delay_off'

sudo sh -c 'echo timer > /sys/class/leds/camsync1-gpio/trigger'
sudo sh -c 'echo 10 > /sys/class/leds/camsync1-gpio/delay_on; echo 20 > /sys/class/leds/camsync1-gpio/delay_off'

exit
EOF


