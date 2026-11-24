# NRAW → CinemaDNG

Windows 64 位桌面工具：将尼康 `.NEV` 视频逐帧解压为未压缩的 CFA CinemaDNG 序列。提供中文界面、命令行、范围选择、磁盘容量检查、逐帧像素回读、停止与校验续转。

## 使用

1. 解压 Windows 便携包，保留整个目录，运行 `NRAW-to-CinemaDNG.exe`。
2. 选择 NEV 文件和你有权使用的 `REDR3D-x64.dll`。
3. 选择**同机型的参考 CFA DNG**。可以用 Adobe DNG Converter 将该机型的一张 NEF 转为 DNG。建议使用同机身、相近 ISO 的原始照片，不要用经过增强、线性化或转换为 RGB 的 DNG。
4. 选择空输出文件夹。先填帧数 `3`，点击“检查素材与空间”，再开始转换。
5. 将输出中的连续编号 `frame_00000000.dng` 等作为图像序列导入支持 CinemaDNG 的软件。

首次转换需要参考 DNG，程序**不内置未经验证的通用色矩阵**。SDK 和 Adobe 相机配置不随本仓库/便携包分发；也不会自动下载或修改它们。

## 保留什么

- 原始解压 Bayer 样本逐值不变，不黑位相减、不白平衡乘法、不裁切重排、不去马赛克、不烘焙 RGB、不附加降噪/锐化。
- 从每一帧读取 RGGB、四相位 BlackLevel、白平衡、原始有效画幅、ISO、曝光、光圈、焦距和方向。
- `AsShotNeutral` 由该视频帧的白平衡有理数计算，不复制参考照片的白平衡。
- 从参考 DNG 继承 ColorMatrix、ForwardMatrix、校准光源、CameraCalibration、AnalogBalance、配置曲线/查找表与曝光基准。**WhiteLevel 读取实际提供的参考文件**，不是按机型写死的数值，也不是推测传感器 ADC 位深。
- 保存完整 CFA 网格，以 DefaultCropOrigin/Size 表达源有效区域；奇数裁切起点不会改坏原始 CFA 相位。
- 从 NEV sample table 读取精确帧率，例如 `30000/1001`。抽帧时输出帧率除以间隔，输出文件仍连续编号，原帧号写入 `frames.jsonl`。中文源文件名使用稳定 ASCII ReelName 别名，原名保留在本地转换记录中。
- 每张 DNG 写入临时文件，像素/关键标签回读通过后提交；禁止覆盖未知已有文件。续转检查源片段哈希与已有 DNG 哈希。

## 支持边界

- 已实测 **Nikon Z8 4K、Z6III 4K/6K** 的 14-bit 解码输出；16-bit TIFF 容器不代表 16-bit 传感器精度。其他相机、CFA、位深和可变帧率输入会拒绝。
- 依赖未修改的 **R3DSDK 9.2.1-7058969 Windows x64** 特定构建，SHA-256：

  `a3c3039647716ebfb254898568dbb0954925e35dcb76a48b24cc58af36ca4b31`

  使用版本绑定的压缩层入口，不是脱离 SDK 的独立解码器；原初始化/授权流程保持。
- 相机配置渲染不等同于 Nikon Picture Control、N-Log 转换或 RED IPP2 外观。参考 DNG 的白位/配置决定默认曝光与高光映射；若照片和视频模式需要不同色彩标定，应提供经过标定的参考 DNG。
- 不复制参考照片的镜头 Opcode、几何裁切、序列号、GPS、缩略图、降噪模型或 MakerNotes；不声称完成原视频镜头畸变校正。
- 当前不导出音频、不复制或伪造源 SMPTE 时间码；保留视频帧率和源帧映射。单帧标准曝光 EXIF 写入文件，完整转换信息另存 JSON。
- **无压缩**：约 `宽 × 高 × 2 × 帧数` 字节，另加少量标签。建议先小范围测试。
- 已验证 TIFF/LibRaw、Adobe DNG Converter 18.7 回读及同条件色彩对照。**尚未完成 DaVinci Resolve / Premiere / Lightroom 界面验收**，不同软件的色彩管理、默认裁切与配置支持可能不同。详见 `VALIDATION.md`。

## 源码运行

Python 3.12+、Windows x64，需 Tk。依赖安装位置及输出目录由用户选择。

```powershell
python -m pip install -r requirements.txt
python -m nraw_cinema gui
python -m nraw_cinema inspect "E:\Video\clip.NEV" --sdk "E:\Tools\REDR3D-x64.dll" --profile "E:\Profiles\camera.dng" --count 3
python -m nraw_cinema convert "E:\Video\clip.NEV" --sdk "E:\Tools\REDR3D-x64.dll" --profile "E:\Profiles\camera.dng" --output "E:\Video\clip_dng" --count 3
```

CLI 的 `--start` 从 0 起，GUI 从 1 起。`--count` 不填表示到片尾；`--step` 默认 1；`--resume` 验证后续转。一次进程只处理一个视频，GUI 每项任务使用隐藏子进程并在结束后释放 SDK。

若进程异常退出留下 `.conversion.lock`，先确认没有写入进程，再手动删除锁。无回执的孤立 DNG 不自动覆盖：移到别处检查后再续转。

## 开发

```powershell
python -m unittest discover -s tests -v
python -m pip install pyinstaller
python -m PyInstaller --noconfirm build_windows.spec
```

GUI 和 worker 必须放在同一目录，并保留 `_internal`。公开测试使用合成数据，不含相机原片、参考配置或 SDK。第三方组件说明见 `THIRD_PARTY.md`。

额外安装 `rawpy` 后，可用 `tools/verify_sequence.py` 对完成的序列进行独立 LibRaw 回读；参数为源 NEV、输出目录、`--sdk` 与 `--profile`。
