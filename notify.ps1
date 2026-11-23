# 每日汇总：Windows 原生 toast 通知（无需第三方库）
param(
  [string]$Title = "HKU 邮件助手",
  [string]$Body  = "有新的邮件汇总",
  [string]$IconPath = ""
)
$ErrorActionPreference = "SilentlyContinue"

try {
  $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
  $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]

  $template = @"
<toast scenario="reminder">
  <visual>
    <binding template="ToastGeneric">
      <text>$([System.Net.WebUtility]::HtmlEncode($Title))</text>
      <text>$([System.Net.WebUtility]::HtmlEncode($Body))</text>
    </binding>
  </visual>
  <audio src="ms-winsoundevent:Notification.Default"/>
</toast>
"@

  $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
  $xml.LoadXml($template)

  $appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
  $toast = New-Object Windows.UI.Notifications.ToastNotification($xml)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
  exit 0
} catch {
  # 兜底：消息框
  try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($Body, $Title) | Out-Null
  } catch { }
  exit 1
}
