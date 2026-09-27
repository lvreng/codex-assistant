idf_component_register(SRCS "main.cpp" "model_picker.cpp" "celestial_device.cpp" "sd_media_player.cpp" "tear_free_display.cpp" "theme_character.cpp" "character_motion.cpp" "character_cache.cpp"
                            "astra_compose.cpp" "astra_compose_p4.S" "device_link.cpp"
                            "generated/character_assets.cpp" "generated/vangogh_scene.cpp" "board_display.c"
                            "font_cn_18.c" "font_cn_30.c" "font_menu_18.c"
                            "font_pixel_16.c" "font_pixel_24.c"
                       INCLUDE_DIRS "."
                       REQUIRES fatfs sdmmc nvs_flash esp_timer esp_mm bt esp_hosted
                                esp_driver_ledc esp_driver_sdmmc esp_driver_jpeg esp_driver_ppa
                                esp_driver_usb_serial_jtag)
