import os
import sys
import subprocess


def main():
    target_script = "merge_gds_ui.py"
    app_name = "GDS_MERGER_1.0"
    icon_png = "icon.png"
    icon_ico = "icon.ico"

    # 1. 检查主程序文件是否存在
    if not os.path.exists(target_script):
        print(f"❌ 错误: 在当前目录下找不到 '{target_script}'！")
        print("请确保打包脚本与你的 UI 脚本在同一个文件夹内。")
        sys.exit(1)

    # 2. 自动检查并安装 PyInstaller 和 Pillow (用于图片转换)
    try:
        import PyInstaller
        print("✅ 检测到已安装 PyInstaller。")
    except ImportError:
        print("⚠️ 未检测到 PyInstaller，正在为你自动安装...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
            print("✅ PyInstaller 安装完成！")
        except Exception as e:
            print(f"❌ 安装 PyInstaller 失败: {e}")
            sys.exit(1)

    try:
        from PIL import Image
        print("✅ 检测到已安装 Pillow (图像处理库)。")
    except ImportError:
        print("⚠️ 未检测到 Pillow，正在为你自动安装以转换图标格式...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
            print("✅ Pillow 安装完成！")
            from PIL import Image
        except Exception as e:
            print(f"❌ 安装 Pillow 失败: {e}")
            sys.exit(1)

    import PyInstaller.__main__

    # 3. 自动转换 PNG 为 ICO
    use_icon = False
    if os.path.exists(icon_png):
        print(f"🔄 正在将 {icon_png} 转换为 Windows 支持的 {icon_ico} 格式...")
        try:
            img = Image.open(icon_png)
            # 生成包含多种尺寸的 ICO 文件，确保在不同分辨率下都清晰
            img.save(icon_ico, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (32, 32)])
            use_icon = True
            print("✅ 图标格式转换成功！")
        except Exception as e:
            print(f"⚠️ 图标转换失败，将忽略图标设置: {e}")
    elif os.path.exists(icon_ico):
        use_icon = True
        print(f"✅ 发现已存在的 {icon_ico}。")
    else:
        print(f"ℹ️ 未在目录下找到 {icon_png}，将使用系统默认图标。")

    # 4. 配置打包参数
    print(f"\n🚀 开始将 {target_script} 打包为独立的 .exe 程序...")

    pyinstaller_args = [
        target_script,
        f'--name={app_name}',           # 生成的 exe 名字
        '--onefile',                    # 将所有依赖打包进一个单一的 .exe 文件中
        '--windowed',                   # 运行时不显示黑色的控制台/命令行窗口
        '--clean',                      # 每次打包前清理之前的缓存
        '--hidden-import=klayout.db',   # 隐式导入 klayout 模块
        '--collect-all=klayout',        # 👈 【核心修复】强制收集 klayout 底层的所有 C++ 动态库和插件！
        '--log-level=WARN',             # 只输出警告和错误信息，保持终端清爽
    ]

    # 如果图标存在且可用，把图标参数加进打包配置里
    if use_icon:
        pyinstaller_args.append(f'--icon={icon_ico}')

    # 5. 执行打包
    try:
        PyInstaller.__main__.run(pyinstaller_args)
        print("\n" + "=" * 50)
        print(f"🎉 打包大功告成！")
        print(f"请在当前目录的 'dist' 文件夹中寻找你的程序: {app_name}.exe")
        print("=" * 50)
    except Exception as e:
        print(f"\n❌ 打包过程中发生错误: {e}")


if __name__ == "__main__":
    main()