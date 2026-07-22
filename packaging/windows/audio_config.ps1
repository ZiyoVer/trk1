# Live Translator — Windows default audio qurilmalarini boshqarish.
#
# IPolicyConfig orqali default IJRO (render) va YOZIB OLISH (capture)
# qurilmasini o'qish/o'rnatish. Ilovaning O'Z sessiyasida ishlaydi.
#
# -Action getdefaults        -> "render=<nom>" va "capture=<nom>"
# -Action setrender -Name X  -> default ijro = X nomli qurilma
# -Action setcapture -Name X -> default yozib olish = X nomli qurilma
# -Action restore            -> ikkalasini birinchi FIZIK qurilmaga qaytaradi

param(
    [string]$Action = "getdefaults",
    [string]$Name = ""
)

$ErrorActionPreference = "Stop"

Add-Type -Language CSharp @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
namespace LTAudio {
  [Guid("f8679f50-850a-41cf-9c72-430f290290c8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPolicyConfig {
    int GetMixFormat(string d, IntPtr f); int GetDeviceFormat(string d, bool b, IntPtr f);
    int ResetDeviceFormat(string d); int SetDeviceFormat(string d, IntPtr a, IntPtr b);
    int GetProcessingPeriod(string d, bool b, IntPtr a, IntPtr c); int SetProcessingPeriod(string d, IntPtr p);
    int GetShareMode(string d, IntPtr m); int SetShareMode(string d, IntPtr m);
    int GetPropertyValue(string d, bool b, ref PKEY k, IntPtr v);
    int SetPropertyValue(string d, bool b, ref PKEY k, IntPtr v);
    int SetDefaultEndpoint(string d, uint role); int SetEndpointVisibility(string d, bool v);
  }
  [StructLayout(LayoutKind.Sequential)] public struct PKEY { public Guid fmtid; public int pid; }
  [ComImport, Guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")] public class CPolicyConfigClient {}
  [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] public class MMDeviceEnumerator {}
  [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int flow, int mask, out IMMDeviceCollection col);
    int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice dev);
  }
  [Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IMMDeviceCollection { int GetCount(out int c); int Item(int i, out IMMDevice d); }
  [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IMMDevice {
    int Activate(ref Guid id, int ctx, IntPtr p, out IntPtr o);
    int OpenPropertyStore(int access, out IPropertyStore store);
    int GetId(out string id); int GetState(out int st);
  }
  [Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPropertyStore {
    int GetCount(out int c); int GetAt(int i, out PKEY k);
    int GetValue(ref PKEY k, out PROPVARIANT v); int SetValue(ref PKEY k, ref PROPVARIANT v); int Commit();
  }
  [StructLayout(LayoutKind.Explicit)] public struct PROPVARIANT { [FieldOffset(0)] public short vt; [FieldOffset(8)] public IntPtr p; }
  public static class Cfg {
    static PKEY NAME = new PKEY { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };
    // 0 = eRender (ijro), 1 = eCapture (yozib olish)
    static string DeviceName(IMMDevice d) {
      IPropertyStore ps; d.OpenPropertyStore(0, out ps);
      PROPVARIANT v; ps.GetValue(ref NAME, out v);
      return Marshal.PtrToStringUni(v.p);
    }
    static bool IsVirtual(string n) {
      if (n == null) return true;
      n = n.ToLowerInvariant();
      return n.Contains("cable") || n.Contains("hi-fi") || n.Contains("hifi")
          || n.Contains("vb-audio") || n.Contains("sound mapper") || n.Contains("переназначение");
    }
    // Bir xil VB-Audio drayverining nechanchi nusxasi ("2- VB-Audio ...").
    static int InstanceNum(string n) {
      if (n == null) return 1;
      var m = System.Text.RegularExpressions.Regex.Match(
        n.ToLowerInvariant(), @"(\d+)-\s*vb-audio");
      int v; if (m.Success && int.TryParse(m.Groups[1].Value, out v)) return v;
      return 1;
    }
    static bool IsHiFi(string n) {
      if (n == null) return false; n = n.ToLowerInvariant();
      return n.Contains("hi-fi") || n.Contains("hifi");
    }
    static bool IsBaseCable(string n) {
      if (n == null) return false; n = n.ToLowerInvariant();
      return !IsHiFi(n) && (n.Contains("cable input") || n.Contains("cable output")
          || n.Contains("vb-audio virtual cable") || n.Contains("vb-cable"));
    }
    static bool IsHeadphone(string n) {
      if (n == null) return false; n = n.ToLowerInvariant();
      return n.Contains("headphone") || n.Contains("headset")
          || n.Contains("наушник") || n.Contains("гарнитур");
    }
    // "hifi:N" / "vbcable:N" — o'sha oiladagi ANIQ nusxa. Aks holda oddiy
    // quyi-satr moslash (eski xatti-harakat).
    static bool NameMatches(string name, string match) {
      if (name == null) return false;
      if (string.IsNullOrEmpty(match)) return true;
      if (match.StartsWith("hifi:")) {
        int inst; if (!int.TryParse(match.Substring(5), out inst)) inst = 1;
        return IsHiFi(name) && InstanceNum(name) == inst;
      }
      if (match.StartsWith("vbcable:")) {
        int inst; if (!int.TryParse(match.Substring(8), out inst)) inst = 1;
        return IsBaseCable(name) && InstanceNum(name) == inst;
      }
      return name.ToLowerInvariant().Contains(match.ToLowerInvariant());
    }
    public static string GetDefaultName(int flow) {
      var en = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
      IMMDevice d;
      if (en.GetDefaultAudioEndpoint(flow, 1 /*eMultimedia*/, out d) != 0 || d == null) return "";
      return DeviceName(d) ?? "";
    }
    static void SetById(string id) {
      var pc = (IPolicyConfig)(new CPolicyConfigClient());
      pc.SetDefaultEndpoint(id, 0); pc.SetDefaultEndpoint(id, 1); pc.SetDefaultEndpoint(id, 2);
    }
    public static string SetDefaultByName(int flow, string match, bool physicalOnly) {
      var en = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
      IMMDeviceCollection col; en.EnumAudioEndpoints(flow, 1 /*ACTIVE*/, out col);
      int n; col.GetCount(out n);
      for (int i = 0; i < n; i++) {
        IMMDevice d; col.Item(i, out d);
        string name = DeviceName(d); string id; d.GetId(out id);
        bool nameOk = NameMatches(name, match);
        if (nameOk && (!physicalOnly || !IsVirtual(name))) { SetById(id); return name; }
      }
      return null;
    }
    // Fizik chiqishga qaytarish: avval naushnik/garnitura, keyin istalgan
    // fizik qurilma (Stop'da ovozni odam eshitadigan joyga qaytaramiz).
    public static string SetDefaultPhysicalPreferred(int flow) {
      var en = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
      IMMDeviceCollection col; en.EnumAudioEndpoints(flow, 1 /*ACTIVE*/, out col);
      int n; col.GetCount(out n);
      for (int pass = 0; pass < 2; pass++) {
        for (int i = 0; i < n; i++) {
          IMMDevice d; col.Item(i, out d);
          string name = DeviceName(d); string id; d.GetId(out id);
          if (IsVirtual(name)) continue;
          if (pass == 0 && !IsHeadphone(name)) continue;
          SetById(id); return name;
        }
      }
      return null;
    }
  }
}
"@

switch ($Action) {
  "getdefaults" {
    Write-Output ("render=" + [LTAudio.Cfg]::GetDefaultName(0))
    Write-Output ("capture=" + [LTAudio.Cfg]::GetDefaultName(1))
  }
  "setrender"  { $r = [LTAudio.Cfg]::SetDefaultByName(0, $Name, $false); if ($r) { "OK: $r" } else { "NOT_FOUND" } }
  "setcapture" { $r = [LTAudio.Cfg]::SetDefaultByName(1, $Name, $false); if ($r) { "OK: $r" } else { "NOT_FOUND" } }
  "restore" {
    # Chiqish: naushnik ulangan bo'lsa o'shanga, aks holda fizik karnayga.
    $r = [LTAudio.Cfg]::SetDefaultPhysicalPreferred(0)
    $c = [LTAudio.Cfg]::SetDefaultByName(1, "", $true)
    "render=" + $r; "capture=" + $c
  }
  "restorerender"  { $r = [LTAudio.Cfg]::SetDefaultPhysicalPreferred(0); if ($r) { "OK: $r" } else { "NOT_FOUND" } }
  "restorecapture" { $c = [LTAudio.Cfg]::SetDefaultByName(1, "", $true); if ($c) { "OK: $c" } else { "NOT_FOUND" } }
  default { "UNKNOWN_ACTION" }
}
