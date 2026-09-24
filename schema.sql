-- =====================================================================
-- PresyoWatch Database Schema (MySQL 8.0+)
-- Real-Time Market Price Monitoring and Price Gouging Alert System
-- =====================================================================

CREATE DATABASE IF NOT EXISTS presyowatch
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE presyowatch;

-- ---------------------------------------------------------------------
-- USERS  (residents, LGU staff, DTI/DA personnel, market vendors, admin)
-- ---------------------------------------------------------------------
CREATE TABLE users (
    user_id         INT AUTO_INCREMENT PRIMARY KEY,
    full_name       VARCHAR(150)  NOT NULL,
    email           VARCHAR(150)  NOT NULL UNIQUE,
    password_hash   VARCHAR(255)  NOT NULL,
    role            ENUM('resident','vendor','lgu_staff','dti_da','admin') NOT NULL DEFAULT 'resident',
    city            ENUM('Cebu City','Lapu-Lapu City') DEFAULT NULL,
    contact_number  VARCHAR(20)   DEFAULT NULL,
    is_verified_vendor BOOLEAN    DEFAULT FALSE,
    subscription_status ENUM('active','inactive','trial') DEFAULT 'trial', -- used for LGU/DTI-DA account access control
    is_active       BOOLEAN       DEFAULT TRUE,
    created_at      DATETIME      DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- MARKETS  (e.g. Carbon Market, Lapu-Lapu City Public Market)
-- ---------------------------------------------------------------------
CREATE TABLE markets (
    market_id       INT AUTO_INCREMENT PRIMARY KEY,
    market_name     VARCHAR(150) NOT NULL,
    city            ENUM('Cebu City','Lapu-Lapu City') NOT NULL,
    latitude        DECIMAL(10,6) DEFAULT NULL,
    longitude       DECIMAL(10,6) DEFAULT NULL
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- PRODUCTS  (basic commodities being tracked)
-- ---------------------------------------------------------------------
CREATE TABLE products (
    product_id      INT AUTO_INCREMENT PRIMARY KEY,
    product_name    VARCHAR(120) NOT NULL UNIQUE,
    unit            VARCHAR(30)  NOT NULL DEFAULT 'kg',   -- kg, piece, tray, liter, etc.
    category        VARCHAR(60)  DEFAULT 'General'
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- DTI SRP (Suggested Retail Price) BASELINE
-- Stands in for the automated DTI web-crawling module described in the
-- study; in production this table is refreshed by a scheduled scraper
-- job (see utils/dti_scraper.py stub) instead of manual entry.
-- ---------------------------------------------------------------------
CREATE TABLE dti_srp (
    srp_id          INT AUTO_INCREMENT PRIMARY KEY,
    product_id      INT NOT NULL,
    srp_price       DECIMAL(10,2) NOT NULL,
    bulletin_date   DATE NOT NULL,
    source          VARCHAR(150) DEFAULT 'DTI SRP Bulletin',
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- PRICE REPORTS  (crowd-sourced submissions from residents/vendors)
-- ---------------------------------------------------------------------
CREATE TABLE price_reports (
    report_id       INT AUTO_INCREMENT PRIMARY KEY,
    user_id         INT NOT NULL,
    product_id      INT NOT NULL,
    market_id       INT NOT NULL,
    price           DECIMAL(10,2) NOT NULL,
    photo_path      VARCHAR(255) DEFAULT NULL,
    report_date     DATE NOT NULL,
    status          ENUM('pending','verified','rejected','flagged') NOT NULL DEFAULT 'pending',
    is_spike        BOOLEAN DEFAULT FALSE,
    spike_percent   DECIMAL(6,2) DEFAULT NULL,
    verified_by     INT DEFAULT NULL,
    verified_at     DATETIME DEFAULT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE,
    FOREIGN KEY (market_id) REFERENCES markets(market_id) ON DELETE CASCADE,
    FOREIGN KEY (verified_by) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- ALERTS  (auto-generated when a price spike / possible gouging is found)
-- ---------------------------------------------------------------------
CREATE TABLE alerts (
    alert_id        INT AUTO_INCREMENT PRIMARY KEY,
    report_id       INT NOT NULL,
    message         VARCHAR(255) NOT NULL,
    severity        ENUM('low','medium','high') NOT NULL DEFAULT 'medium',
    is_resolved     BOOLEAN DEFAULT FALSE,
    resolved_by     INT DEFAULT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at     DATETIME DEFAULT NULL,
    FOREIGN KEY (report_id) REFERENCES price_reports(report_id) ON DELETE CASCADE,
    FOREIGN KEY (resolved_by) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- GENERATED REPORTS LOG  (PDF/CSV exports produced for DTI/DA submission)
-- ---------------------------------------------------------------------
CREATE TABLE report_exports (
    export_id       INT AUTO_INCREMENT PRIMARY KEY,
    generated_by    INT NOT NULL,
    file_type       ENUM('pdf','csv') NOT NULL,
    file_path       VARCHAR(255) NOT NULL,
    date_from       DATE NOT NULL,
    date_to         DATE NOT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (generated_by) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- SEED DATA
-- ---------------------------------------------------------------------
INSERT INTO markets (market_name, city, latitude, longitude) VALUES
('Carbon Market', 'Cebu City', 10.2951, 123.8969),
('Lapu-Lapu City Public Market', 'Lapu-Lapu City', 10.3103, 123.9494),
('Pasil Market', 'Cebu City', 10.3010, 123.9050),
('Mandaue City Public Market', 'Cebu City', 10.3236, 123.9226);

INSERT INTO products (product_name, unit, category) VALUES
('Rice (Well-Milled)', 'kg', 'Grains'),
('Egg (Medium)', 'piece', 'Poultry'),
('Chicken (Whole)', 'kg', 'Poultry'),
('Pork (Kasim)', 'kg', 'Meat'),
('Beef (Brisket)', 'kg', 'Meat'),
('Tilapia', 'kg', 'Fish'),
('Galunggong', 'kg', 'Fish'),
('Tomato', 'kg', 'Vegetables'),
('Onion (Red)', 'kg', 'Vegetables'),
('Garlic', 'kg', 'Vegetables');

INSERT INTO dti_srp (product_id, srp_price, bulletin_date, source) VALUES
(1, 48.00, '2026-07-01', 'DTI SRP Bulletin'),
(2, 7.50,  '2026-07-01', 'DTI SRP Bulletin'),
(3, 190.00,'2026-07-01', 'DTI SRP Bulletin'),
(4, 320.00,'2026-07-01', 'DTI SRP Bulletin'),
(5, 380.00,'2026-07-01', 'DTI SRP Bulletin'),
(6, 140.00,'2026-07-01', 'DTI SRP Bulletin'),
(7, 160.00,'2026-07-01', 'DTI SRP Bulletin'),
(8, 80.00, '2026-07-01', 'DTI SRP Bulletin'),
(9, 90.00, '2026-07-01', 'DTI SRP Bulletin'),
(10,110.00,'2026-07-01', 'DTI SRP Bulletin');

-- Default admin account (password: Admin@123 -- change after first login)
-- password_hash below is a placeholder; the app will create/reset this via seed_admin.py
