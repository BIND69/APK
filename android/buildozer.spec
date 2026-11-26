[app]
title = World
package.name = worldlan
package.domain = org.tural.world
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.4.0
requirements = python3,kivy==2.3.0,pyjnius
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CHANGE_WIFI_MULTICAST_STATE,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,READ_MEDIA_IMAGES,READ_MEDIA_VIDEO,READ_MEDIA_AUDIO
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
