# Live Translator — Windows default karnayni FIZIK qurilmaga qaytaradi.
#
# VB-CABLE va Hi-Fi Cable drayverlari o'rnatilganda o'zlarini Windows'ning
# default karnayi qilib qo'yadi -> ovoz haqiqiy karnayga bormay qoladi.
# Bu skript IPolicyConfig orqali default ijro qurilmasini birinchi FIZIK
# (virtual bo'lmagan) qurilmaga qaytaradi. Ilovaning O'Z sessiyasida
# ishlaydi (audio-COM shu yerda ishlaydi).
#
# Ixtiyoriy -Match: aynan shu nom bo'lagini o'z ichiga olgan qurilma.

param([string]$Match = "")

$ErrorActionPreference = "Stop"

Add-Type -Language CSharp @"
using System;
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
  public static class Config {
    static PKEY NAME = new PKEY { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };
    static bool IsVirtual(string n) {
      if (n == null) return true;
      n = n.ToLowerInvariant();
      return n.Contains("cable") || n.Contains("hi-fi") || n.Contains("hifi")
          || n.Contains("vb-audio") || n.Contains("sound mapper")
          || n.Contains("переназначение"); // "Переназначение"
    }
    public static string Restore(string match) {
      var en = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
      IMMDeviceCollection col; en.EnumAudioEndpoints(0 /*eRender*/, 1 /*DEVICE_STATE_ACTIVE*/, out col);
      int n; col.GetCount(out n);
      for (int i = 0; i < n; i++) {
        IMMDevice d; col.Item(i, out d);
        IPropertyStore ps; d.OpenPropertyStore(0, out ps);
        PROPVARIANT v; ps.GetValue(ref NAME, out v);
        string name = Marshal.PtrToStringUni(v.p);
        string id; d.GetId(out id);
        bool okName = string.IsNullOrEmpty(match)
          ? !IsVirtual(name)
          : (name != null && name.ToLowerInvariant().Contains(match.ToLowerInvariant()) && !IsVirtual(name));
        if (okName) {
          var pc = (IPolicyConfig)(new CPolicyConfigClient());
          pc.SetDefaultEndpoint(id, 0); // eConsole
          pc.SetDefaultEndpoint(id, 1); // eMultimedia
          pc.SetDefaultEndpoint(id, 2); // eCommunications
          return name;
        }
      }
      return null;
    }
  }
}
"@

$result = [LTAudio.Config]::Restore($Match)
if ($result) { Write-Output ("OK: " + $result) } else { Write-Output "NO_PHYSICAL_DEVICE" }
