// SPDX-License-Identifier: GPL-2.0-or-later

import Darwin
import Foundation
import GPTFDisk

nonisolated enum PersistentDisk {
    static let gib: UInt64 = 1024 * 1024 * 1024

    static func capacity(_ image: URL) throws -> UInt64 {
        let values = try FileManager.default.attributesOfItem(atPath: image.path)
        guard let size = values[.size] as? NSNumber else {
            throw GuestSessionError.message("Cannot read the disk capacity.")
        }
        return size.uint64Value
    }

    static func grow(_ image: URL, to requested: UInt64) throws {
        guard image.pathExtension.lowercased() == "img", requested <= UInt64(Int64.max),
              requested > (try capacity(image)) else {
            throw GuestSessionError.message("Choose a capacity larger than the current disk.")
        }
        let manager = FileManager.default
        let values = try manager.attributesOfFileSystem(forPath: image.deletingLastPathComponent().path)
        if let available = values[.systemFreeSize] as? NSNumber,
           available.uint64Value < 16 * 1024 * 1024 {
            throw GuestSessionError.message("There is not enough free space to grow the disk safely.")
        }
        var before = kernarium_gptfdisk_partition_info()
        let errorCapacity = 2048
        var error = [CChar](repeating: 0, count: errorCapacity)
        let read = kernarium_gptfdisk_read_partition(image.path, 1, &before, &error, errorCapacity)
        guard read == 0 else { throw GuestSessionError.message(String(cString: error)) }

        let staging = image.deletingLastPathComponent().appendingPathComponent(".disk-growth-\(UUID().uuidString)")
        try manager.createDirectory(at: staging, withIntermediateDirectories: false)
        defer { try? manager.removeItem(at: staging) }
        let copy = staging.appendingPathComponent(image.lastPathComponent)
        if clonefile(image.path, copy.path, 0) != 0 {
            if manager.fileExists(atPath: copy.path) { try manager.removeItem(at: copy) }
            try sparseCopy(image, to: copy)
        }
        let descriptor = open(copy.path, O_RDWR | O_CLOEXEC)
        guard descriptor >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        defer { Darwin.close(descriptor) }
        guard ftruncate(descriptor, off_t(requested)) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        var after = kernarium_gptfdisk_partition_info()
        // Standard GPT leaves 32 partition-entry sectors before its final
        // header. GPT fdisk takes the distance from that final header.
        let result = kernarium_gptfdisk_grow_partition_to_fill(copy.path, 1,
            UInt64(before.block_size) * 33, &after, &error, errorCapacity)
        guard result == 0 else { throw GuestSessionError.message(String(cString: error)) }
        guard before.first_lba == after.first_lba,
              before.block_size == after.block_size,
              after.last_lba > before.last_lba,
              (after.backup_header_lba + 1) * UInt64(after.block_size) == requested else {
            throw GuestSessionError.message("The enlarged disk failed GPT verification.")
        }
        guard fsync(descriptor) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        // rename is the only operation on the original path. All allocation,
        // truncation, GPT writes and verification completed on the copy.
        guard rename(copy.path, image.path) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }

    private static func sparseCopy(_ source: URL, to destination: URL) throws {
        let input = open(source.path, O_RDONLY | O_CLOEXEC)
        guard input >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        defer { Darwin.close(input) }
        let output = open(destination.path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0o600)
        guard output >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        defer { Darwin.close(output) }
        let length = try capacity(source)
        var position: off_t = 0
        var buffer = [UInt8](repeating: 0, count: 1024 * 1024)
        while UInt64(position) < length {
            let count = buffer.withUnsafeMutableBytes {
                pread(input, $0.baseAddress!, $0.count, position)
            }
            guard count > 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
            if buffer.prefix(count).contains(where: { $0 != 0 }) {
                var offset = 0
                while offset < count {
                    let written = buffer.withUnsafeBytes {
                        pwrite(output, $0.baseAddress!.advanced(by: offset), count - offset, position + off_t(offset))
                    }
                    guard written > 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
                    offset += written
                }
            }
            position += off_t(count)
        }
        guard ftruncate(output, off_t(length)) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }
}

enum DiskGrowthSettings {
    static func pending(filename: String, defaults: UserDefaults = .standard) -> UInt64? {
        let value = defaults.string(forKey: "persistentDiskCapacity.\(filename)")
        return value.flatMap(UInt64.init)
    }

    static func schedule(bytes: UInt64?, filename: String, defaults: UserDefaults = .standard) {
        let key = "persistentDiskCapacity.\(filename)"
        if let bytes { defaults.set(String(bytes), forKey: key) }
        else { defaults.removeObject(forKey: key) }
    }
}
