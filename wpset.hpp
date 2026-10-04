// wpset.hpp - header-only library for per-monitor wallpaper assignment on Windows.
//
// Wraps the shell's IDesktopWallpaper COM interface, the same component that
// backs the per-monitor background picker in the Settings application.
// Assignments are therefore persistent system state, not a transient overlay.
//
// Monitor identification: the shell addresses each display by its device
// interface path (e.g. "\\?\DISPLAY#..."). This library additionally exposes
// a zero-based enumeration index that mirrors GetMonitorDevicePathAt() order;
// note that enumeration order is shell-defined and need not match physical
// left-to-right placement.
//
// Error policy: all failures (COM bring-up, enumeration, missing files, bad
// indices) are reported by throwing std::runtime_error / std::out_of_range.
//
// Ownership & threading: the constructor initializes COM on the calling
// thread's apartment if (and only if) it is not yet initialized, and the
// destructor tears it down only in that case (RAII). Instances are movable,
// non-copyable, and not thread-safe.
//
// Link requirements: ole32 + uuid. MSVC picks them up via pragmas below;
// MinGW/GCC needs: -lole32 -luuid
//
// Library usage:
//   #include "wpset.hpp"
//   wpset::wpset ws;
//   ws.assign({ { L"a.png", 0 }, { L"b.png", 1 }, { L"c.png", 2 } });

#ifndef WPSET_HPP_INCLUDED
#define WPSET_HPP_INCLUDED

#include <windows.h>
#include <shobjidl_core.h>

#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>

#ifdef _MSC_VER
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "uuid.lib")
#endif

namespace wpset {

class wpset {
public:
    // A display as visible to the shell wallpaper API.
    struct monitor {
        unsigned     index;       // zero-based shell enumeration order
        std::wstring device_path; // device interface path (monitor ID)
    };

    // Brings up COM (if required) and instantiates the shell object.
    wpset() {
        // S_FALSE: apartment already initialized by the host; we then borrow
        // it and must not uninitialize on destruction.
        const HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
        if (hr == RPC_E_CHANGED_MODE)
            throw std::runtime_error("wpset: COM already initialized with an incompatible (MTA) threading model");
        check(hr, "CoInitializeEx");
        com_owner_ = (hr == S_OK);

        const HRESULT cr = CoCreateInstance(CLSID_DesktopWallpaper, nullptr, CLSCTX_ALL,
                                            IID_IDesktopWallpaper,
                                            reinterpret_cast<void**>(&shell_));
        if (FAILED(cr)) {
            if (com_owner_) CoUninitialize();
            shell_ = nullptr;
            com_owner_ = false;
            check(cr, "CoCreateInstance(CLSID_DesktopWallpaper)");
        }
    }

    ~wpset() {
        if (shell_) shell_->Release();
        if (com_owner_) CoUninitialize();
    }

    wpset(const wpset&) = delete;
    wpset& operator=(const wpset&) = delete;

    wpset(wpset&& other) noexcept
        : shell_(other.shell_), com_owner_(other.com_owner_) {
        other.shell_ = nullptr;
        other.com_owner_ = false;
    }

    wpset& operator=(wpset&& other) noexcept {
        if (this != &other) {
            if (shell_) shell_->Release();
            if (com_owner_) CoUninitialize();
            shell_ = other.shell_;
            com_owner_ = other.com_owner_;
            other.shell_ = nullptr;
            other.com_owner_ = false;
        }
        return *this;
    }

    // Number of displays currently visible to the shell.
    unsigned monitor_count() const {
        UINT n = 0;
        check(shell_->GetMonitorDevicePathCount(&n), "GetMonitorDevicePathCount");
        return static_cast<unsigned>(n);
    }

    // Enumerates displays in shell order.
    std::vector<monitor> monitors() const {
        const unsigned n = monitor_count();
        std::vector<monitor> result;
        result.reserve(n);
        for (unsigned i = 0; i < n; ++i) {
            LPWSTR id = nullptr;
            check(shell_->GetMonitorDevicePathAt(i, &id), "GetMonitorDevicePathAt");
            result.push_back(monitor{ i, id ? id : L"" });
            CoTaskMemFree(id);  // allocated by the COM task allocator
        }
        return result;
    }

    // Assigns an image to a display addressed by enumeration index.
    void set_wallpaper(unsigned monitor_index, const std::wstring& image_path) const {
        set_wallpaper(device_path_at(monitor_index), image_path);
    }

    // Assigns an image to a display addressed by device interface path.
    // The shell rejects relative paths, so the input is canonicalized first.
    void set_wallpaper(const std::wstring& device_path, const std::wstring& image_path) const {
        if (!file_exists(image_path))
            throw std::runtime_error("wpset: image not found: " + narrow(image_path));
        const std::wstring full = absolute(image_path);
        check(shell_->SetWallpaper(device_path.c_str(), full.c_str()), "SetWallpaper");
    }

    // Batch assignment of (image path, monitor index) pairs.
    void assign(const std::vector<std::pair<std::wstring, unsigned>>& mapping) const {
        for (const auto& entry : mapping)
            set_wallpaper(entry.second, entry.first);
    }

    // Convenience: image k -> monitor k, for k = 0..n-1.
    void assign_sequential(const std::vector<std::wstring>& image_paths) const {
        if (image_paths.size() > monitor_count())
            throw std::out_of_range("wpset: more images than connected monitors");
        for (unsigned i = 0; i < image_paths.size(); ++i)
            set_wallpaper(i, image_paths[i]);
    }

private:
    // Resolves an enumeration index to the corresponding monitor ID.
    std::wstring device_path_at(unsigned index) const {
        if (index >= monitor_count())
            throw std::out_of_range("wpset: monitor index out of range");
        LPWSTR id = nullptr;
        check(shell_->GetMonitorDevicePathAt(index, &id), "GetMonitorDevicePathAt");
        std::wstring result = id ? id : L"";
        CoTaskMemFree(id);
        return result;
    }

    // Translates a failing HRESULT into a thrown, human-readable error.
    static void check(HRESULT hr, const char* operation) {
        if (!FAILED(hr)) return;
        char buffer[160];
        std::snprintf(buffer, sizeof buffer, "wpset: %s failed (HRESULT 0x%08lX)",
                      operation, static_cast<unsigned long>(hr));
        throw std::runtime_error(buffer);
    }

    static bool file_exists(const std::wstring& path) {
        return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES;
    }

    // Relative -> absolute path canonicalization (shell requirement).
    static std::wstring absolute(const std::wstring& path) {
        wchar_t buffer[MAX_PATH] = {};
        const DWORD len = GetFullPathNameW(path.c_str(), MAX_PATH, buffer, nullptr);
        return (len != 0 && len < MAX_PATH) ? std::wstring(buffer, len) : path;
    }

    // Wide -> narrow conversion for exception messages only.
    static std::string narrow(const std::wstring& text) {
        if (text.empty()) return {};
        const int needed = WideCharToMultiByte(CP_ACP, 0, text.c_str(), -1,
                                               nullptr, 0, nullptr, nullptr);
        std::string out(static_cast<size_t>(needed) - 1, '\0');
        WideCharToMultiByte(CP_ACP, 0, text.c_str(), -1,
                            &out[0], needed, nullptr, nullptr);
        return out;
    }

    IDesktopWallpaper* shell_ = nullptr;
    bool com_owner_ = false;  // true only if this instance initialized COM
};

} // namespace wpset

#endif // WPSET_HPP_INCLUDED