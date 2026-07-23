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
  // DIQQAT: har metodda [PreserveSig] SHART (aks holda .NET yashirin
  // [retval] qo'shib vtable chaqiruvини siljitadi va XOTIRA BUZILADI —
  // 0xC0000374). VA ikki xil vtable bor:
  //   * IPolicyConfigVista (568b9108) — Vista/Win10/11, ResetDeviceFormat'SIZ,
  //     SetDefaultEndpoint 10-metod.
  //   * IPolicyConfig (f8679f50) — Win7, ResetDeviceFormat BILAN, 11-metod.
  // Noto'g'ri variant chaqirilsa slot mos kelmay crash bo'ladi. Shuning
  // uchun ikkalasini ham e'lon qilamiz va OS qo'llab-quvvatlaganini
  // ("as" orqali) tanlaymiz.
  [Guid("568b9108-44bb-40b4-a6ee-901400770e28"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPolicyConfigVista {
    [PreserveSig] int GetMixFormat(string d, IntPtr f);
    [PreserveSig] int GetDeviceFormat(string d, int b, IntPtr f);
    [PreserveSig] int SetDeviceFormat(string d, IntPtr a, IntPtr b);
    [PreserveSig] int GetProcessingPeriod(string d, int b, IntPtr a, IntPtr c);
    [PreserveSig] int SetProcessingPeriod(string d, IntPtr p);
    [PreserveSig] int GetShareMode(string d, IntPtr m);
    [PreserveSig] int SetShareMode(string d, IntPtr m);
    [PreserveSig] int GetPropertyValue(string d, IntPtr k, IntPtr v);
    [PreserveSig] int SetPropertyValue(string d, IntPtr k, IntPtr v);
    [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string d, uint role);
    [PreserveSig] int SetEndpointVisibility(string d, int v);
  }
  [Guid("f8679f50-850a-41cf-9c72-430f290290c8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPolicyConfig {
    [PreserveSig] int GetMixFormat(string d, IntPtr f);
    [PreserveSig] int GetDeviceFormat(string d, int b, IntPtr f);
    [PreserveSig] int ResetDeviceFormat(string d);
    [PreserveSig] int SetDeviceFormat(string d, IntPtr a, IntPtr b);
    [PreserveSig] int GetProcessingPeriod(string d, int b, IntPtr a, IntPtr c);
    [PreserveSig] int SetProcessingPeriod(string d, IntPtr p);
    [PreserveSig] int GetShareMode(string d, IntPtr m);
    [PreserveSig] int SetShareMode(string d, IntPtr m);
    [PreserveSig] int GetPropertyValue(string d, IntPtr k, IntPtr v);
    [PreserveSig] int SetPropertyValue(string d, IntPtr k, IntPtr v);
    [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string d, uint role);
    [PreserveSig] int SetEndpointVisibility(string d, int v);
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
    // DIQQAT: LPWStr SHART. Default marshaling (BSTR) — GetId CoTaskMemAlloc
    // string qaytaradi, .NET uni SysFreeString bilan bo'shatib XOTIRANI
    // BUZADI (0xC0000374). Shu bitta atribut butun routing crash'ining sababi.
    int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id); int GetState(out int st);
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
          || n.Contains("наушник") || n.Contains("гарнитур")
          || n.Contains("quloqchin") || n.Contains("airpods")
          || n.Contains("earbud") || n.Contains("hands-free")
          || n.Contains("handsfree");
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
      // OS qo'llab-quvvatlaган variantни tanlaymiz. "as" QueryInterface
      // qiladi: Vista (Win10/11) topilsa null bo'lmaydi; aks holda (Win7)
      // legacy'ga tushamiz. Noto'g'ri vtable chaqirilmagani uchun crash yo'q.
      object client = new CPolicyConfigClient();
      // Legacy (f8679f50) BIRINCHI — real Win10/11 mashinada aynan shu
      // qo'llab-quvvatlanadi va sinovdan o'tgan. Vista (568b9108) faqat
      // legacy bo'lmagan tizimlar uchun zaxira.
      var legacy = client as IPolicyConfig;
      if (legacy != null) {
        legacy.SetDefaultEndpoint(id, 0); legacy.SetDefaultEndpoint(id, 1); legacy.SetDefaultEndpoint(id, 2);
        return;
      }
      var vista = client as IPolicyConfigVista;
      if (vista != null) {
        vista.SetDefaultEndpoint(id, 0); vista.SetDefaultEndpoint(id, 1); vista.SetDefaultEndpoint(id, 2);
      }
    }
    // Ko'p-kanalli spatial variant ("CABLE In 16ch", "... 8ch"). Bunday
    // endpoint kabelning STANDART "CABLE Output"iga ULANMAYDI — video unga
    // o'ynasa, ilova jimlikni eshitadi. Standart 2ch "CABLE Input" kerak.
    static bool IsSpatialVariant(string n) {
      if (n == null) return false;
      return System.Text.RegularExpressions.Regex.IsMatch(
        n.ToLowerInvariant(), @"\b\d+\s*ch\b");
    }
    public static string SetDefaultByName(int flow, string match, bool physicalOnly) {
      var en = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
      IMMDeviceCollection col; en.EnumAudioEndpoints(flow, 1 /*ACTIVE*/, out col);
      int n; col.GetCount(out n);
      // IKKI O'TISH: avval STANDART endpoint (kanal-son qo'shimchasisiz),
      // keyingina spatial variant. Aks holda "CABLE In 16ch" birinchi
      // topilib, video kabelga tushmay, tarjima umuman ishlamasdi.
      for (int pass = 0; pass < 2; pass++) {
        for (int i = 0; i < n; i++) {
          IMMDevice d; col.Item(i, out d);
          string name = DeviceName(d); string id; d.GetId(out id);
          if (!NameMatches(name, match)) continue;
          if (physicalOnly && IsVirtual(name)) continue;
          if (pass == 0 && IsSpatialVariant(name)) continue;
          SetById(id); return name;
        }
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
    // Default'ni O'ZGARTIRMASDAN, tarjima chiqishi uchun eng mos ACTIVE fizik
    // qurilma NOMINI qaytaradi: avval naushnik/garnitura (ulangan bo'lsa),
    // keyin istalgan fizik karnay. Faqat ACTIVE endpoint'lar sanaladi —
    // shuning uchun naushnik topilsa = HAQIQATAN ulangan (bo'sh uya ACTIVE
    // emas). Ilova incoming tarjimani shu qurilmaga chiqaradi.
    public static string FindPhysicalPreferred(int flow) {
      var en = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
      IMMDeviceCollection col; en.EnumAudioEndpoints(flow, 1 /*ACTIVE*/, out col);
      int n; col.GetCount(out n);
      for (int pass = 0; pass < 2; pass++) {
        for (int i = 0; i < n; i++) {
          IMMDevice d; col.Item(i, out d);
          string name = DeviceName(d);
          if (IsVirtual(name)) continue;
          if (pass == 0 && !IsHeadphone(name)) continue;
          return name;
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
  "findoutput" { $r = [LTAudio.Cfg]::FindPhysicalPreferred(0); if ($r) { $r } else { "" } }
  default { "UNKNOWN_ACTION" }
}
